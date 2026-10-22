"""切塊方式比較與選擇：檢索方法固定為版本 A（bge-m3 向量），只改切塊。

用法：
    EMBED_BACKEND=ollama python scripts/select_chunking.py --out docs/chunking-selection-raw.md

選擇規則（先寫好再看結果，避免「看了數字再找理由」）：
  1. 開發集 dev（tests/retrieval_eval.jsonl，16 題）用來選；測試集 test（難題 12 題）只用來確認，不拿來挑。
  2. 主要指標：Recall@K（等預算：切塊展開成段落後取前 K 段）。生成需要「所有」相關段落，跨段落題只找到一段不夠，
     所以用 Recall 而不是 Hit。
  3. 和基準 chunk-para 比較時，用成對 bootstrap 算「差值」的 95% 信賴區間：下限 > 0 才算顯著較好。
     只有 16 題，差 1 題就是 0.06，不做這一步很容易把雜訊當成進步。
  4. 沒有顯著較好的 → 留在 chunk-para（最簡單：一塊一段，sources 不必推算歸屬）。
     有多個 → 取 dev Recall 最高；平手看 MRR；再平手看實際送進 LLM 的字數（少者優）。
  5. 選出的方法在 test 上若顯著比 chunk-para 差（信賴區間上限 < 0），就退回 chunk-para。
"""

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

from app import retrieval  # noqa: E402

BASE = "chunk-para"
DEPTH = 10


def load(path: str) -> list[dict]:
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def evaluate(name: str, cases: list[dict], k: int) -> list[dict]:
    """每題的指標。等預算看前 k 段；「實際」看系統 retrieve(k) 真正送進 LLM 的段落。"""
    cfg = retrieval.PRESETS[name]
    paras = retrieval.load_paragraphs()
    rows = []
    for c in cases:
        want = set(c["expect_ids"])
        t = time.perf_counter()
        ranking: list[str] = []
        for chunk, _ in retrieval.rank_chunks(c["question"], cfg):
            ranking += [p for p in chunk.covered_ids if p not in ranking]
        ms = (time.perf_counter() - t) * 1000
        real = [s.id for s in retrieval.retrieve(c["question"], k, name)]
        first = next((i for i, p in enumerate(ranking[:DEPTH], 1) if p in want), None)
        rows.append(
            dict(
                recall=len(want & set(ranking[:k])) / len(want),
                hit=float(bool(want & set(ranking[:k]))),
                rr=1 / first if first else 0.0,
                real_recall=len(want & set(real)) / len(want),
                n_src=len(real),
                chars=sum(len(paras[p].text) for p in real),
                ms=ms,
            )
        )
    return rows


def mean(rows: list[dict], key: str) -> float:
    return statistics.mean(r[key] for r in rows)


def paired_ci(a: list[dict], b: list[dict], key: str, n_boot: int, rng: random.Random) -> tuple[float, float, float]:
    """b − a 的平均差與 95% bootstrap 信賴區間（同一題成對重抽）。"""
    diffs = [y[key] - x[key] for x, y in zip(a, b)]
    boots = sorted(statistics.mean(rng.choices(diffs, k=len(diffs))) for _ in range(n_boot))
    return statistics.mean(diffs), boots[int(0.025 * n_boot)], boots[int(0.975 * n_boot) - 1]


def table(title: str, res: dict[str, list[dict]], k: int, n_boot: int, seed: int) -> tuple[list[str], dict]:
    rng = random.Random(seed)
    cis = {}
    out = [
        f"### {title}",
        "",
        f"| 切塊 | 塊數 | Recall@{k} | 與 {BASE} 的差（95% CI） | Hit@{k} | MRR | 實際 sources 數 | 實際 Recall | 送進 LLM 字數 | 每題(ms) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, rows in res.items():
        n_chunks = len(retrieval.get_index(retrieval.PRESETS[name]).chunks)
        if name == BASE:
            ci_txt = "—（基準）"
        else:
            d, lo, hi = paired_ci(res[BASE], rows, "recall", n_boot, rng)
            cis[name] = (d, lo, hi)
            mark = "　**顯著較好**" if lo > 0 else ("　**顯著較差**" if hi < 0 else "")
            ci_txt = f"{d:+.2f}（{lo:+.2f}, {hi:+.2f}）{mark}"
        out.append(
            f"| {name} | {n_chunks} | {mean(rows, 'recall'):.2f} | {ci_txt} | {mean(rows, 'hit'):.2f} | "
            f"{mean(rows, 'rr'):.2f} | {mean(rows, 'n_src'):.1f} | {mean(rows, 'real_recall'):.2f} | "
            f"{mean(rows, 'chars'):.0f} | {mean(rows, 'ms'):.0f} |"
        )
    return out, cis


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", default=str(ROOT / "tests" / "retrieval_eval.jsonl"))
    ap.add_argument("--test", default=str(ROOT / "tests" / "retrieval_eval_hard.jsonl"))
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--boot", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--presets", nargs="*", default=list(retrieval.CHUNK_PRESETS))
    ap.add_argument("--out")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    names = [BASE] + [n for n in args.presets if n != BASE]

    dev, test = load(args.dev), load(args.test)
    res_dev, res_test = {}, {}
    for name in names:
        retrieval.get_index(retrieval.PRESETS[name])  # 建索引不計入每題延遲
        res_dev[name] = evaluate(name, dev, args.k)
        res_test[name] = evaluate(name, test, args.k)
        print(f"done {name}", file=sys.stderr)

    dev_lines, dev_ci = table(f"開發集（{len(dev)} 題，用來選）", res_dev, args.k, args.boot, args.seed)
    test_lines, test_ci = table(f"測試集（{len(test)} 題，只用來確認）", res_test, args.k, args.boot, args.seed)

    better = [n for n, (_, lo, _) in dev_ci.items() if lo > 0]
    if better:
        key = lambda n: (mean(res_dev[n], "recall"), mean(res_dev[n], "rr"), -mean(res_dev[n], "chars"))  # noqa: E731
        pick = max(better, key=key)
        reason = f"開發集上顯著優於 {BASE} 的有：{', '.join(better)}；依 Recall → MRR → 字數選出 {pick}。"
        if test_ci[pick][2] < 0:
            reason += f" 但 {pick} 在測試集顯著較差，退回 {BASE}。"
            pick = BASE
    else:
        pick = BASE
        reason = f"開發集上沒有任何切塊方式的 Recall@{args.k} 顯著優於 {BASE}，依規則留在最簡單的 {BASE}。"
    worse = [n for n, (_, _, hi) in dev_ci.items() if hi < 0]

    text = "\n".join(
        [
            f"嵌入來源 EMBED_BACKEND={retrieval.EMBED_BACKEND}；K={args.k}；bootstrap {args.boot} 次（seed={args.seed}）。",
            "",
            *dev_lines,
            "",
            *test_lines,
            "",
            "### 選擇結果",
            "",
            f"- **選出：{pick}**",
            f"- 理由：{reason}",
            f"- 開發集上顯著較差：{', '.join(worse) or '無'}",
        ]
    )
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
