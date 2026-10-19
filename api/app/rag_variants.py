"""比較實驗用的檢索方法，Docker 映像不會用到（需要 torch／transformers）。

hyde        讓 LLM 先寫一段「假想的原文」，把它和問題的向量平均後再找（HyDE）
multiquery  讓 LLM 把問題改寫成 3 種說法，各自做向量檢索，再以 RRF 合併
m3sparse    bge-m3 的稀疏輸出：模型自己學出每個詞的權重（可想成「學出來的 BM25」）
m3colbert   bge-m3 的多向量輸出：問題每個 token 找文件中最像的 token 再平均（late interaction）

LLM 來源（環境變數 LLM_BACKEND，預設同 EMBED_BACKEND）：
    ollama  主機 Ollama 的 LOCAL_MODEL（預設 qwen2.5:3b）
    st      transformers 載入 Qwen/Qwen2.5-1.5B-Instruct（本機 CPU 實驗用）
設 LLM_CACHE=<檔案路徑> 可把生成結果存檔，重跑實驗時不必重新生成。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from . import retrieval as R

LLM_BACKEND = os.environ.get("LLM_BACKEND", R.EMBED_BACKEND)
LOCAL_MODEL = os.environ.get("LOCAL_MODEL", "qwen2.5:3b")
HF_LLM = "Qwen/Qwen2.5-1.5B-Instruct"
LLM_CACHE = os.environ.get("LLM_CACHE")

HYDE_PROMPT = (
    "以下是關於一所國中本學期校務的問題。請假設你手上有學校的會議紀錄，"
    "用會議紀錄的書面語寫一段約 80 字、能回答這個問題的內容。只輸出那段內容，不要解釋。\n\n問題：{q}"
)
MULTI_PROMPT = (
    "請把下面這個關於國中校務的問題，改寫成 3 種意思相同但用詞不同的問法，"
    "盡量使用學校公文常見的用語。每行一個，不要編號、不要其他文字。\n\n問題：{q}"
)


# ---------------------------------------------------------------- LLM

_cache: dict[str, str] | None = None
_hf = None


def _load_cache() -> dict[str, str]:
    global _cache
    if _cache is None:
        _cache = {}
        if LLM_CACHE and Path(LLM_CACHE).exists():
            _cache = json.loads(Path(LLM_CACHE).read_text(encoding="utf-8"))
    return _cache


def llm(prompt: str) -> str:
    cache = _load_cache()
    key = f"{LLM_BACKEND}|{LOCAL_MODEL if LLM_BACKEND == 'ollama' else HF_LLM}|{prompt}"
    if key in cache:
        return cache[key]
    if LLM_BACKEND == "ollama":
        resp = httpx.post(
            f"{R.OLLAMA_URL}/api/generate",
            json={"model": LOCAL_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0}},
            timeout=300,
        )
        resp.raise_for_status()
        out = resp.json()["response"]
    else:
        global _hf
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        if _hf is None:
            tok = AutoTokenizer.from_pretrained(HF_LLM)
            _hf = (tok, AutoModelForCausalLM.from_pretrained(HF_LLM, dtype=torch.bfloat16))
        tok, model = _hf
        text = tok.apply_chat_template([{"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True)
        ids = tok(text, return_tensors="pt")
        with torch.no_grad():
            gen = model.generate(**ids, max_new_tokens=150, do_sample=False)
        out = tok.decode(gen[0, ids.input_ids.shape[1] :], skip_special_tokens=True)
    out = out.strip()
    cache[key] = out
    if LLM_CACHE:
        Path(LLM_CACHE).write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def hyde_text(query: str) -> str:
    return llm(HYDE_PROMPT.format(q=query))


def rewrites(query: str) -> list[str]:
    lines = [l.strip(" -•\t0123456789.、") for l in llm(MULTI_PROMPT.format(q=query)).splitlines()]
    return [l for l in lines if l][:3]


# ---------------------------------------------------------------- bge-m3 稀疏／多向量

_m3 = None
_m3_docs: dict[str, list[dict]] = {}


def _m3_model():
    global _m3
    if _m3 is None:
        import torch
        from huggingface_hub import hf_hub_download
        from transformers import AutoModel, AutoTokenizer

        tok = AutoTokenizer.from_pretrained("BAAI/bge-m3")
        model = AutoModel.from_pretrained("BAAI/bge-m3").eval()
        sparse = torch.nn.Linear(1024, 1)
        colbert = torch.nn.Linear(1024, 1024)
        sparse.load_state_dict(torch.load(hf_hub_download("BAAI/bge-m3", "sparse_linear.pt"), map_location="cpu"))
        colbert.load_state_dict(torch.load(hf_hub_download("BAAI/bge-m3", "colbert_linear.pt"), map_location="cpu"))
        skip = {tok.cls_token_id, tok.eos_token_id, tok.pad_token_id, tok.unk_token_id}
        _m3 = (tok, model, sparse, colbert, skip)
    return _m3


def m3_encode(texts: list[str]) -> list[dict]:
    """與 FlagEmbedding 的 BGEM3FlagModel 相同的算法：
    sparse = relu(線性層(每個 token 的隱藏向量))，同一 token 取最大值；
    colbert = 正規化(線性層(去掉 [CLS] 後每個 token 的隱藏向量))。"""
    import torch

    tok, model, sparse_lin, colbert_lin, skip = _m3_model()
    out = []
    for t in texts:
        batch = tok([t], truncation=True, max_length=512, return_tensors="pt")
        with torch.no_grad():
            h = model(**batch).last_hidden_state[0]
            w = torch.relu(sparse_lin(h)).squeeze(-1)
            col = torch.nn.functional.normalize(colbert_lin(h[1:]), dim=-1)
        weights: dict[int, float] = {}
        for tid, wt in zip(batch.input_ids[0].tolist(), w.tolist()):
            if tid not in skip and wt > weights.get(tid, 0):
                weights[tid] = wt
        out.append({"sparse": weights, "colbert": col})
    return out


def _doc_m3(idx: R.Index) -> list[dict]:
    key = idx.chunks[0].chunk_id + str(len(idx.chunks))
    if key not in _m3_docs:
        _m3_docs[key] = m3_encode([c.text for c in idx.chunks])
    return _m3_docs[key]


def m3_tokens(text: str) -> dict[str, float]:
    """把稀疏權重轉回文字，方便看模型認為哪些詞重要。"""
    tok = _m3_model()[0]
    w = m3_encode([text])[0]["sparse"]
    return {tok.decode([i]): round(v, 3) for i, v in sorted(w.items(), key=lambda p: -p[1])}


# ---------------------------------------------------------------- 對外：計分


def scores(method: str, query: str, cfg: dict, idx: R.Index) -> list[float]:
    if method == "hyde":
        qv, hv = R.embed([query, hyde_text(query)], cfg["embed"])
        q = R._normalize([a + b for a, b in zip(qv, hv)])
        return [R._dot(q, v) for v in idx.vectors]
    if method == "multiquery":
        queries = [query] + rewrites(query)
        vecs = R.embed(queries, cfg["embed"])
        n = len(idx.chunks)
        rankings = [sorted(range(n), key=lambda i, q=q: -R._dot(q, idx.vectors[i])) for q in vecs]
        fused = R.rrf(rankings)
        return [fused[i] for i in range(n)]
    if method in ("m3sparse", "m3colbert"):
        q = m3_encode([query])[0]
        docs = _doc_m3(idx)
        if method == "m3sparse":
            return [sum(w * d["sparse"].get(t, 0.0) for t, w in q["sparse"].items()) for d in docs]
        return [float((q["colbert"] @ d["colbert"].T).max(dim=1).values.mean()) for d in docs]
    raise ValueError(f"未知的檢索方法：{method}")
