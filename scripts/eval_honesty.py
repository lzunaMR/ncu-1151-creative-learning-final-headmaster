"""評估「找不到就說不知道」：相似度門檻與可回答性檢查，在有答案／沒有答案的題目上各會怎樣。

用法（需要 Ollama）：
    EMBED_BACKEND=ollama python scripts/eval_honesty.py --out docs/honesty-experiments-raw.md

最重要的錯誤是「誤判不知道」：資料明明有答案卻回答不知道。其次才是「漏判」：資料沒有答案卻放行給模型生成。
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

from app import honesty, retrieval  # noqa: E402


def load(name: str, has_answer: bool) -> list[dict]:
    rows = [json.loads(l) for l in open(ROOT / "tests" / name, encoding="utf-8") if l.strip()]
    for r in rows:
        r["has_answer"] = has_answer
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=["qwen2.5:1.5b", "qwen2.5:3b"])
    ap.add_argument("--threshold", type=float, default=honesty.SIM_THRESHOLD)
    ap.add_argument("--out")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")

    cases = load("retrieval_eval.jsonl", True) + load("retrieval_eval_hard.jsonl", True) + load("no_answer_eval.jsonl", False)
    for c in cases:
        c["sim"] = honesty.top_similarity(c["question"])
        c["sources"] = retrieval.retrieve(c["question"])
    results = {}
    for m in args.models:
        honesty.judge("暖機", [], model=m)
        for c in cases:
            t = time.perf_counter()
            c[m] = honesty.judge(c["question"], c["sources"], model=m)
            c[m + "_ms"] = (time.perf_counter() - t) * 1000
        print(f"done {m}", file=sys.stderr)

    yes = [c for c in cases if c["has_answer"]]
    no = [c for c in cases if not c["has_answer"]]

    def blocked(c, mode):
        gate = c["sim"] < args.threshold
        if mode == "gate":
            return gate
        return gate or c[mode] is False

    out = [
        f"有答案 {len(yes)} 題（兩份檢索評測集）、沒有答案 {len(no)} 題（tests/no_answer_eval.jsonl）；相似度門檻 {args.threshold}。",
        "",
        "| 方式 | 誤判不知道（有答案卻擋下） | 抓到沒有答案的（完全無關） | 抓到沒有答案的（主題相近） | 每題多花(ms) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for mode in ["gate", *args.models]:
        fp = sum(blocked(c, mode) for c in yes)
        off = [c for c in no if c["type"] == "完全無關"]
        near = [c for c in no if c["type"] != "完全無關"]
        ms = "0" if mode == "gate" else f"{statistics.mean(c[mode + '_ms'] for c in cases):.0f}"
        name = "只用相似度門檻" if mode == "gate" else f"門檻＋{mode} 可回答性檢查"
        out.append(
            f"| {name} | {fp}/{len(yes)} | {sum(blocked(c, mode) for c in off)}/{len(off)} | "
            f"{sum(blocked(c, mode) for c in near)}/{len(near)} | {ms} |"
        )
    out += ["", "| 題目 | 類型 | 最高相似度 | " + " | ".join(args.models) + " |", "| --- " * (3 + len(args.models)) + "|"]
    for c in sorted(cases, key=lambda c: c["sim"]):
        kind = "有答案" if c["has_answer"] else c["type"]
        cells = [{True: "能", False: "不能", None: "（不合法）"}[c[m]] for m in args.models]
        out.append(f"| {c['question']} | {kind} | {c['sim']:.3f} | " + " | ".join(cells) + " |")
    text = "\n".join(out)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
