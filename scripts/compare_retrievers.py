"""方法比較實驗：對評測集跑 retrieval.PRESETS 中的每一種設定，一次只差一個變因。

用法（本機，需 sentence-transformers；Docker 內沒有）：
    EMBED_BACKEND=st python scripts/compare_retrievers.py --out docs/retrieval-experiments-raw.md
    EMBED_BACKEND=st python scripts/compare_retrievers.py --presets A B bm25-bigram

指標（皆以「段落」為單位：切塊展開成段落 id 後依序去重，再取前 K 個）：
    Hit@K     Top-K 含任一應命中 id 的題數比例（與 §4 的「命中」定義相同）
    Recall@K  應命中 id 被找回的比例，對跨段落題比 Hit 嚴格
    MRR       第一個應命中 id 名次的倒數平均（看前 10 名），反映「排多前面」
另外列出每題第一個應命中段落的名次，用來找失敗案例。
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

from app import retrieval  # noqa: E402

KS = (1, 3, 5)
DEPTH = 10


def para_ranking(query: str, cfg: dict) -> list[str]:
    ids: list[str] = []
    for chunk, _ in retrieval.rank_chunks(query, cfg):
        for pid in chunk.covered_ids:
            if pid not in ids:
                ids.append(pid)
    return ids


def first_rank(ranking: list[str], expect: list[str]) -> int | None:
    for i, pid in enumerate(ranking[:DEPTH], 1):
        if pid in expect:
            return i
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(ROOT / "tests" / "retrieval_eval.jsonl"))
    ap.add_argument("--presets", nargs="*", default=list(retrieval.PRESETS))
    ap.add_argument("--out")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")

    cases = [json.loads(l) for l in open(args.file, encoding="utf-8") if l.strip()]
    rows, ranks, tops = [], {}, {}
    for name in args.presets:
        cfg = retrieval.PRESETS[name]
        t0 = time.perf_counter()
        idx = retrieval.get_index(cfg)
        build = time.perf_counter() - t0  # 含模型載入
        retrieval.rank_chunks("暖機", cfg)  # 讓重排序模型等先載入，延遲才公平
        lat, rankings = [], []
        for c in cases:
            t = time.perf_counter()
            rankings.append(para_ranking(c["question"], cfg))
            lat.append(time.perf_counter() - t)
        m = {"preset": name, "chunks": len(idx.chunks), "build_s": build, "ms": statistics.mean(lat) * 1000}
        for k in KS:
            hit = [bool(set(r[:k]) & set(c["expect_ids"])) for r, c in zip(rankings, cases)]
            rec = [len(set(r[:k]) & set(c["expect_ids"])) / len(c["expect_ids"]) for r, c in zip(rankings, cases)]
            m[f"hit{k}"], m[f"rec{k}"] = sum(hit), statistics.mean(rec)
        rr = [first_rank(r, c["expect_ids"]) for r, c in zip(rankings, cases)]
        m["mrr"] = statistics.mean(1 / x if x else 0 for x in rr)
        rows.append(m)
        ranks[name] = rr
        tops[name] = [r[:3] for r in rankings]
        print(f"done {name}", file=sys.stderr)

    n = len(cases)
    cfg_desc = lambda p: ", ".join(f"{k}={v}" for k, v in retrieval.PRESETS[p].items())  # noqa: E731
    out = [f"評測集 {n} 題；嵌入來源 EMBED_BACKEND={retrieval.EMBED_BACKEND}。", ""]
    out += ["| 設定 | 內容 | 切塊數 | Hit@1 | Hit@3 | Hit@5 | Recall@3 | Recall@5 | MRR | 建索引(s) | 每題(ms) |"]
    out += ["| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for m in rows:
        out.append(
            f"| {m['preset']} | {cfg_desc(m['preset'])} | {m['chunks']} | {m['hit1']}/{n} | {m['hit3']}/{n} | "
            f"{m['hit5']}/{n} | {m['rec3']:.2f} | {m['rec5']:.2f} | {m['mrr']:.2f} | {m['build_s']:.1f} | {m['ms']:.0f} |"
        )
    out += ["", f"每題第一個應命中段落的名次（前 {DEPTH} 名內，— 代表沒進前 {DEPTH}）", ""]
    out += ["| 題號 | 類型 | 問題 | 應命中 | " + " | ".join(args.presets) + " |"]
    out += ["| --- " * (4 + len(args.presets)) + "|"]
    for i, c in enumerate(cases):
        cells = [str(ranks[p][i] or "—") for p in args.presets]
        out.append(f"| {c['id']} | {c.get('type', '')} | {c['question']} | {', '.join(c['expect_ids'])} | " + " | ".join(cells) + " |")
    out += ["", "各設定每題 Top-3 段落", ""]
    out += ["| 題號 | " + " | ".join(args.presets) + " |", "| --- " * (1 + len(args.presets)) + "|"]
    for i, c in enumerate(cases):
        out.append(f"| {c['id']} | " + " | ".join(", ".join(tops[p][i]) for p in args.presets) + " |")
    text = "\n".join(out)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
