"""校務文件檢索（RAG 的切塊、索引、檢索）。

對外介面：
    retrieve(query, k=None, version=None, trace=None) -> list[Source]

版本（環境變數 RETRIEVER_VERSION，預設 A；函式參數 version 優先）：
    A  基本版：一個 ### 段落＝一個切塊，向量檢索（bge-m3，餘弦相似度）
    B  改良版：切塊與嵌入模型同 A，再加 BM25（字元二元組）以 RRF 合併
其餘 PRESETS 為方法比較實驗用（scripts/compare_retrievers.py），也能用同一個參數指定。

嵌入來源（環境變數 EMBED_BACKEND）：
    ollama  預設；Docker 內使用，呼叫主機上的 Ollama /api/embed
    st      本機實驗用；sentence-transformers 從 HuggingFace 載入同名模型
嵌入失敗時改用 BM25，並在 trace 如實記錄。
"""

from __future__ import annotations

import math
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .schemas import Source, TraceStep

DOCS_DIR = Path(os.environ.get("PRINCIPAL_DOCS_DIR", "/data/principal/docs"))
if not DOCS_DIR.exists():  # 不在 Docker 內時，改用 repo 內的資料
    DOCS_DIR = Path(__file__).resolve().parents[2] / "data" / "principal" / "docs"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")  # 不用 localhost：Windows 會先試 IPv6 ::1，每次連線多等約 2 秒
EMBED_BACKEND = os.environ.get("EMBED_BACKEND", "ollama")
DEFAULT_K = int(os.environ.get("RETRIEVER_K", "3"))

# 嵌入模型簡稱 → (Ollama 名稱, HuggingFace 名稱)。Ollama 沒有的只能用 st 跑實驗
EMBED_MODELS = {
    "bge-m3": ("bge-m3", "BAAI/bge-m3"),
    "bge-small-zh": (None, "BAAI/bge-small-zh-v1.5"),
    "minilm": (None, "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"),
}
RERANK_MODEL = "BAAI/bge-reranker-base"

# 每個設定只改需要比較的變因；A、B 之間只差 method
PRESETS: dict[str, dict] = {
    "A": dict(chunk="para", method="dense", embed="bge-m3"),
    "B": dict(chunk="para", method="hybrid", embed="bge-m3", tokenizer="bigram"),
    # --- 以下為比較實驗 ---
    "bm25-bigram": dict(chunk="para", method="bm25", tokenizer="bigram"),
    "bm25-jieba": dict(chunk="para", method="bm25", tokenizer="jieba"),
    "dense-bge-small": dict(chunk="para", method="dense", embed="bge-small-zh"),
    "dense-minilm": dict(chunk="para", method="dense", embed="minilm"),
    "dense-ctx": dict(chunk="para_ctx", method="dense", embed="bge-m3"),
    "dense-section": dict(chunk="section", method="dense", embed="bge-m3"),
    "dense-window": dict(chunk="window", method="dense", embed="bge-m3"),
    "hybrid-weighted": dict(chunk="para", method="weighted", embed="bge-m3", tokenizer="bigram"),
    "hybrid-jieba": dict(chunk="para", method="hybrid", embed="bge-m3", tokenizer="jieba"),
    "B+rerank": dict(chunk="para", method="hybrid", embed="bge-m3", tokenizer="bigram", rerank=True),
    # --- 第二輪：不需 LLM ---
    "dense-sent2para": dict(chunk="sentence", method="dense", embed="bge-m3"),
    "dense-mmr": dict(chunk="para", method="dense", embed="bge-m3", mmr=0.7),
    "A+expand": dict(chunk="para", method="dense", embed="bge-m3", expand=True),
    "bm25-expand": dict(chunk="para", method="bm25", tokenizer="bigram", expand=True),
    "B+expand": dict(chunk="para", method="hybrid", embed="bge-m3", tokenizer="bigram", expand=True),
    # --- 第二輪：LLM 改寫查詢（rag_variants.py） ---
    "hyde": dict(chunk="para", method="hyde", embed="bge-m3"),
    "multiquery": dict(chunk="para", method="multiquery", embed="bge-m3"),
    # --- 第二輪：bge-m3 的稀疏與多向量輸出（rag_variants.py） ---
    "m3-sparse": dict(chunk="para", method="m3sparse"),
    "m3-colbert": dict(chunk="para", method="m3colbert"),
    "A+m3sparse": dict(chunk="para", method="hybrid", embed="bge-m3", fuse=("dense", "m3sparse")),
}

# 第四輪：切塊方式比較（scripts/select_chunking.py）。檢索方法都和 A 相同，只改切塊
CHUNK_PRESETS: dict[str, dict] = {
    "chunk-para": dict(chunk="para"),
    "chunk-para_ctx": dict(chunk="para_ctx"),
    "chunk-para_merge-100": dict(chunk="para_merge", size=100),
    "chunk-sentence": dict(chunk="sentence"),
    "chunk-section": dict(chunk="section"),
    **{f"chunk-window-{n}-o{o}": dict(chunk="window", size=n, overlap=o) for n in (60, 120, 200) for o in (0, n // 3)},
    "chunk-recursive-100": dict(chunk="recursive", size=100),
    "chunk-recursive-200": dict(chunk="recursive", size=200),
    "chunk-semantic-p30": dict(chunk="semantic", size=200, threshold=30),
    "chunk-semantic-p50": dict(chunk="semantic", size=200, threshold=50),
}
for _cfg in CHUNK_PRESETS.values():
    _cfg.update(method="dense", embed="bge-m3")
PRESETS.update(CHUNK_PRESETS)

HEADING_RE = re.compile(r"^(#{1,3})\s+(\S+)\s+(.*)$")
PARA_ID_RE = re.compile(r"^S\d+(-\d+){2}$")


# ---------------------------------------------------------------- 切塊


@dataclass
class Paragraph:
    id: str
    doc: str  # 文件標題，如「S1 星河國中……校務會議紀錄」
    section: str  # ## 標題，如「S1-2 教務處報告」
    heading: str  # ### 標題文字（不含 id）
    body: str

    @property
    def text(self) -> str:
        return f"{self.heading}\n{self.body}"


@dataclass
class Chunk:
    chunk_id: str
    text: str  # 拿去嵌入與比對的文字
    covered_ids: list[str] = field(default_factory=list)


def load_paragraphs(docs_dir: Path = DOCS_DIR) -> dict[str, Paragraph]:
    """解析每份 md：### 標題前的編號就是段落 id。說明用的 > 行略過。"""
    paras: dict[str, Paragraph] = {}
    for path in sorted(docs_dir.glob("*.md")):
        doc = section = ""
        current: Paragraph | None = None
        lines: list[str] = []

        def flush():
            if current:
                current.body = "\n".join(lines).strip()
                paras[current.id] = current

        for line in path.read_text(encoding="utf-8").splitlines():
            m = HEADING_RE.match(line)
            if m:
                level, pid, title = len(m.group(1)), m.group(2), m.group(3)
                if level == 1:
                    doc = f"{pid} {title}"
                elif level == 2:
                    section = f"{pid} {title}"
                if level <= 3:
                    flush()
                    current, lines = None, []
                if level == 3 and PARA_ID_RE.match(pid):
                    current = Paragraph(pid, doc, section, title, "")
            elif current and line.strip() and not line.startswith(">"):
                lines.append(line.strip())
        flush()
    return paras


def _doc_streams(paras: dict[str, Paragraph], sep: str = "\n") -> list[tuple[str, str, list[str]]]:
    """每份文件串成一條文字，並記住每個字元屬於哪個段落：(文件代號, 文字, owner)。"""
    by_doc: dict[str, list[Paragraph]] = {}
    for p in paras.values():
        by_doc.setdefault(p.doc, []).append(p)
    out = []
    for doc, ps in by_doc.items():
        text, owner = "", []
        for p in ps:
            piece = p.text + sep
            text += piece
            owner += [p.id] * len(piece)
        out.append((doc.split()[0], text, owner))
    return out


def _span_chunk(cid: str, text: str, owner: list[str], start: int, end: int) -> Chunk:
    # 跨段的切塊依各段佔的字數排序：只沾到上一段尾巴時，主要段落仍排第一
    share = Counter(owner[start:end])
    return Chunk(cid, text[start:end].strip(), sorted(share, key=lambda pid: -share[pid]))


def _recursive_spans(text: str, start: int, end: int, size: int, seps: tuple[str, ...]) -> list[tuple[int, int]]:
    """遞迴切割：先用最粗的分隔符切，太長的片段再用下一層分隔符切，最後才硬切。"""
    if end - start <= size:
        return [(start, end)]
    if not seps:
        return [(i, min(i + size, end)) for i in range(start, end, size)]
    sep, pieces, i = seps[0], [], start
    while i < end:
        j = text.find(sep, i, end)
        j = end if j == -1 else j + len(sep)
        pieces += _recursive_spans(text, i, j, size, seps[1:]) if j - i > size else [(i, j)]
        i = j
    return pieces


def _merge_spans(spans: list[tuple[int, int]], size: int) -> list[tuple[int, int]]:
    """把相鄰的小片段合併到不超過 size。"""
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and b - merged[-1][0] <= size:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans, i = [], 0
    for m in re.finditer(r"[。！？；]|\n", text):
        if text[i : m.end()].strip():
            spans.append((i, m.end()))
        i = m.end()
    if text[i:].strip():
        spans.append((i, len(text)))
    return spans


def chunk_documents(
    strategy: str = "para", size: int = 120, overlap: int = 40, threshold: float = 30, embed_model: str = "bge-m3"
) -> list[Chunk]:
    """回傳切塊清單。size／overlap 單位為字元。

    依文件結構切：
      para        一個 ### 段落一塊（標題＋內文）
      para_ctx    同 para，但前面加上「文件 > 章節」標題，讓短段落帶有上下文
      para_merge  同一章節內，太短的段落與下一段合併，直到至少 size 字
      sentence    一句一塊（small-to-big：用句子比對，回傳時展開成整段）
      section     一個 ## 章節一塊（合併其下所有段落）
    不看文件結構、只看文字切（跨段的切塊依字數佔比記錄 covered_ids）：
      window      固定 size 字的滑動視窗，相鄰視窗重疊 overlap 字
      recursive   依「空行 → 換行 → 句號 → 逗號」遞迴切到不超過 size 字，再把相鄰小片段合併
      semantic    先切成句子，相鄰兩句的向量相似度低於該文件第 threshold 百分位數時斷開；每塊不超過 size 字
    """
    paras = load_paragraphs()
    if strategy == "para":
        return [Chunk(p.id, p.text, [p.id]) for p in paras.values()]
    if strategy == "para_ctx":
        return [Chunk(p.id, f"{p.doc} > {p.section}\n{p.text}", [p.id]) for p in paras.values()]
    if strategy == "para_merge":
        chunks, buf = [], []
        items = list(paras.values())
        for i, p in enumerate(items):
            buf.append(p)
            nxt = items[i + 1] if i + 1 < len(items) else None
            if sum(len(x.text) for x in buf) >= size or not nxt or nxt.section != p.section:
                chunks.append(Chunk("+".join(x.id for x in buf), "\n".join(x.text for x in buf), [x.id for x in buf]))
                buf = []
        return chunks
    if strategy == "sentence":
        chunks = []
        for p in paras.values():
            sents = [p.heading] + [x for x in re.split(r"(?<=[。！？；])|\n", p.body) if x and x.strip()]
            chunks += [Chunk(f"{p.id}#s{i}", t.strip(), [p.id]) for i, t in enumerate(sents)]
        return chunks
    if strategy == "section":
        groups: dict[str, list[Paragraph]] = {}
        for p in paras.values():
            groups.setdefault(p.section, []).append(p)
        return [
            Chunk(sec.split()[0], sec + "\n" + "\n".join(p.text for p in ps), [p.id for p in ps])
            for sec, ps in groups.items()
        ]
    if strategy == "window":
        if not 0 <= overlap < size:
            raise ValueError("overlap 需小於 size")
        chunks = []
        for doc, text, owner in _doc_streams(paras):
            start, n = 0, 0
            while start < len(text):
                end = min(start + size, len(text))
                chunks.append(_span_chunk(f"{doc}-w{n}", text, owner, start, end))
                if end == len(text):
                    break
                start, n = end - overlap, n + 1
        return chunks
    if strategy == "recursive":
        chunks = []
        for doc, text, owner in _doc_streams(paras, sep="\n\n"):
            spans = _merge_spans(_recursive_spans(text, 0, len(text), size, ("\n\n", "\n", "。", "，")), size)
            chunks += [_span_chunk(f"{doc}-r{n}", text, owner, a, b) for n, (a, b) in enumerate(spans)]
        return chunks
    if strategy == "semantic":
        chunks = []
        for doc, text, owner in _doc_streams(paras):
            sents = _sentence_spans(text)
            vecs = embed([text[a:b] for a, b in sents], embed_model)
            sims = [_dot(vecs[i], vecs[i + 1]) for i in range(len(sents) - 1)]
            cut = sorted(sims)[int(len(sims) * threshold / 100)] if sims else 0
            groups, cur = [], [sents[0]]
            for i, sp in enumerate(sents[1:]):
                if sims[i] < cut or sp[1] - cur[0][0] > size:
                    groups.append(cur)
                    cur = []
                cur.append(sp)
            groups.append(cur)
            chunks += [_span_chunk(f"{doc}-s{n}", text, owner, g[0][0], g[-1][1]) for n, g in enumerate(groups)]
        return chunks
    raise ValueError(f"未知的切塊方式：{strategy}")


# ---------------------------------------------------------------- 查詢擴展

# 原文用語 → 一般人可能的說法。只依文件用語整理，寫於難題評測集（tests/retrieval_eval_hard.jsonl）之前；
# 但撰寫者看過原本 16 題，對那 16 題的結果會偏樂觀
SYNONYMS: dict[str, list[str]] = {
    "代理教師": ["代課", "代課老師", "新老師", "換老師"],
    "育嬰留職停薪": ["育嬰假", "請長假", "留停", "請假"],
    "任課教師": ["老師", "授課老師"],
    "學習扶助": ["補救教學", "課後輔導", "補強", "輔導班", "補習"],
    "段考": ["考試", "期中考", "期末考", "月考"],
    "全英語授課": ["全英文", "英文上課", "不用中文"],
    "英語": ["英文"],
    "晨讀": ["閱讀", "看書", "課外書", "讀書"],
    "實驗室": ["實驗教室", "設備", "教室"],
    "補充教材": ["講義", "額外教材", "參考書"],
    "讀者劇場": ["英文比賽", "話劇", "戲劇", "表演"],
    "書面通知": ["通知家長", "告知家長", "發通知"],
    "教學研究會": ["教研會", "科會", "開會"],
    "篩選": ["名單", "挑選", "資格"],
    "會考": ["國中會考", "升學考試"],
}


def expand_query(query: str) -> str:
    """問題裡出現一般說法時，在後面補上原文用語。"""
    extra = [formal for formal, casual in SYNONYMS.items() if formal not in query and any(c in query for c in casual)]
    return query + (" " + " ".join(extra) if extra else "")


# ---------------------------------------------------------------- 斷詞與 BM25

_jieba = None


def tokenize(text: str, how: str = "bigram") -> list[str]:
    """bigram：中文取相鄰兩字，英數（如 805、16:10）保留整串；jieba：搜尋引擎模式斷詞。"""
    text = text.lower()
    if how == "jieba":
        global _jieba
        if _jieba is None:
            import jieba

            jieba.setLogLevel(60)
            _jieba = jieba
        return [t for t in _jieba.lcut_for_search(text) if t.strip() and re.search(r"\w", t)]
    tokens = re.findall(r"[a-z0-9]+(?:[:.\-][a-z0-9]+)*", text)
    for run in re.findall(r"[一-鿿]+", text):
        tokens += [run[i : i + 2] for i in range(len(run) - 1)] or [run]
    return tokens


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.docs = [Counter(d) for d in docs]
        self.lens = [len(d) for d in docs]
        self.avg = sum(self.lens) / len(docs)
        df = Counter(t for d in self.docs for t in d)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.k1, self.b = k1, b

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for tf, dl in zip(self.docs, self.lens):
            s = 0.0
            for t in query:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avg))
            out.append(s)
        return out


# ---------------------------------------------------------------- 嵌入


class EmbedError(RuntimeError):
    pass


_st_models: dict[str, object] = {}


_query_cache: dict[tuple, list[float]] = {}


def embed(texts: list[str], model: str, backend: str | None = None) -> list[list[float]]:
    """回傳 L2 正規化後的向量，內積即餘弦相似度。單一句子（問句）會快取，避免同一題重算。"""
    backend = backend or EMBED_BACKEND
    if len(texts) == 1:
        key = (model, backend, texts[0])
        if key not in _query_cache:
            if len(_query_cache) > 512:
                _query_cache.clear()
            _query_cache[key] = _embed(texts, model, backend)[0]
        return [_query_cache[key]]
    return _embed(texts, model, backend)


def _embed(texts: list[str], model: str, backend: str) -> list[list[float]]:
    ollama_name, hf_name = EMBED_MODELS[model]
    if backend == "st":
        if hf_name not in _st_models:
            from sentence_transformers import SentenceTransformer

            _st_models[hf_name] = SentenceTransformer(hf_name)
        vecs = _st_models[hf_name].encode(texts, normalize_embeddings=True)
        return [list(map(float, v)) for v in vecs]
    if ollama_name is None:
        raise EmbedError(f"{model} 沒有 Ollama 版本，請設 EMBED_BACKEND=st")
    try:
        resp = httpx.post(f"{OLLAMA_URL}/api/embed", json={"model": ollama_name, "input": texts}, timeout=120)
        resp.raise_for_status()
        vecs = resp.json()["embeddings"]
    except (httpx.HTTPError, KeyError) as e:
        raise EmbedError(f"Ollama 嵌入失敗（{ollama_name}）：{e}") from e
    return [_normalize(v) for v in vecs]


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


_reranker = None


def rerank_scores(query: str, texts: list[str]) -> list[float]:
    """Cross-encoder 重排序，只在本機實驗（需 sentence-transformers）使用。"""
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder

        _reranker = CrossEncoder(RERANK_MODEL)
    return [float(s) for s in _reranker.predict([(query, t) for t in texts])]


# ---------------------------------------------------------------- 索引與檢索


@dataclass
class Index:
    chunks: list[Chunk]
    vectors: list[list[float]] | None = None
    bm25: dict[str, BM25] = field(default_factory=dict)
    build_seconds: float = 0.0


_indexes: dict[tuple, Index] = {}


def get_index(cfg: dict) -> Index:
    """同一組（切塊方式、嵌入模型）只建一次索引，放在記憶體。"""
    chunk_args = {k: cfg[k] for k in ("size", "overlap", "threshold") if k in cfg}
    key = (cfg["chunk"], tuple(sorted(chunk_args.items())), cfg.get("embed"), EMBED_BACKEND)
    if key not in _indexes:
        t0 = time.perf_counter()
        idx = Index(chunk_documents(cfg["chunk"], embed_model=cfg.get("embed") or "bge-m3", **chunk_args))
        if cfg.get("embed"):
            idx.vectors = embed([c.text for c in idx.chunks], cfg["embed"])
        idx.build_seconds = time.perf_counter() - t0
        _indexes[key] = idx
    idx = _indexes[key]
    tok = cfg.get("tokenizer")
    if tok and tok not in idx.bm25:
        idx.bm25[tok] = BM25([tokenize(c.text, tok) for c in idx.chunks])
    return idx


def rrf(rankings: list[list[int]], c: int = 60) -> dict[int, float]:
    """Reciprocal Rank Fusion：只看名次不看分數，避免兩種分數尺度不同。"""
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, i in enumerate(ranking):
            fused[i] = fused.get(i, 0.0) + 1 / (c + rank + 1)
    return fused


def _minmax(xs: list[float]) -> list[float]:
    lo, hi = min(xs), max(xs)
    return [(x - lo) / (hi - lo) if hi > lo else 0.0 for x in xs]


def _method_scores(method: str, query: str, cfg: dict, idx: Index) -> list[float]:
    """單一方法對每個切塊的分數。dense、bm25 在這裡；其餘實驗方法在 rag_variants.py。"""
    if method == "dense":
        q = embed([query], cfg["embed"])[0]
        return [_dot(q, v) for v in idx.vectors]
    if method == "bm25":
        return idx.bm25[cfg["tokenizer"]].scores(tokenize(query, cfg["tokenizer"]))
    from . import rag_variants  # 需要 transformers，只在本機實驗載入

    return rag_variants.scores(method, query, cfg, idx)


def mmr(order: list[int], vectors: list[list[float]], rel: list[float], lam: float, n: int = 10) -> list[int]:
    """Maximal Marginal Relevance：每次挑「相關但和已選的不像」的切塊，讓結果涵蓋不同段落。"""
    pool, picked = order[:n], []
    while pool:
        best = max(
            pool,
            key=lambda i: lam * rel[i] - (1 - lam) * max((_dot(vectors[i], vectors[j]) for j in picked), default=0),
        )
        picked.append(best)
        pool.remove(best)
    return picked + order[n:]


def rank_chunks(query: str, cfg: dict) -> list[tuple[Chunk, float]]:
    """依設定把所有切塊排序，回傳 (切塊, 分數)，分數越大越相關。"""
    idx = get_index(cfg)
    n = len(idx.chunks)
    method = cfg["method"]
    if cfg.get("expand"):
        query = expand_query(query)

    def by(scores):
        return sorted(range(n), key=lambda i: -scores[i])

    if method == "hybrid":
        fused = rrf([by(_method_scores(m, query, cfg, idx)) for m in cfg.get("fuse", ("dense", "bm25"))])
        scores = [fused[i] for i in range(n)]
    elif method == "weighted":
        alpha = cfg.get("alpha", 0.5)
        dense, sparse = (_minmax(_method_scores(m, query, cfg, idx)) for m in ("dense", "bm25"))
        scores = [alpha * d + (1 - alpha) * s for d, s in zip(dense, sparse)]
    else:
        scores = _method_scores(method, query, cfg, idx)

    order = by(scores)
    if cfg.get("mmr"):
        order = mmr(order, idx.vectors, scores, cfg["mmr"])
    if cfg.get("rerank"):
        top_n = order[: cfg.get("rerank_n", 10)]
        rs = rerank_scores(query, [idx.chunks[i].text for i in top_n])
        pairs = sorted(zip(top_n, rs), key=lambda p: -p[1])
        return [(idx.chunks[i], s) for i, s in pairs]
    return [(idx.chunks[i], scores[i]) for i in order]


def resolve_version(version: str | None) -> str:
    v = version or os.environ.get("RETRIEVER_VERSION", "A")
    if v not in PRESETS:
        raise ValueError(f"RETRIEVER_VERSION 必須是 {', '.join(PRESETS)} 之一，目前為 {v!r}")
    return v


def retrieve(
    query: str, k: int | None = None, version: str | None = None, trace: list[TraceStep] | None = None
) -> list[Source]:
    """取 Top-K 切塊，展開成段落（一塊涵蓋多段時每段各一筆，依序去重）。"""
    k = k or DEFAULT_K
    v = resolve_version(version)
    cfg = PRESETS[v]
    note = ""
    try:
        ranked = rank_chunks(query, cfg)
    except EmbedError as e:
        # 嵌入不可用時退回 BM25，trace 照實寫
        fallback = dict(chunk=cfg["chunk"], method="bm25", tokenizer=cfg.get("tokenizer", "bigram"))
        ranked = rank_chunks(query, fallback)
        note = f"（{e}；改用 BM25）"

    paras = load_paragraphs()
    ids: list[str] = []
    for chunk, _ in ranked[:k]:
        for pid in chunk.covered_ids:
            if pid not in ids:
                ids.append(pid)
    sources = [Source(id=pid, text=paras[pid].text) for pid in ids]
    if trace is not None:
        trace.append(TraceStep(step="retrieve", detail=f"版本{v} K={k} 命中 {', '.join(ids) or '無'}{note}"))
    return sources


def warmup() -> None:
    """啟動時先建好預設版本的索引；失敗不擋啟動，第一次查詢時會再試。"""
    try:
        get_index(PRESETS[resolve_version(None)])
    except Exception as e:  # noqa: BLE001
        print(f"[retrieval] 預建索引失敗，將於第一次查詢時重試：{e}")
