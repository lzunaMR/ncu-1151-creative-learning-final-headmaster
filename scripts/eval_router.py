"""比較不同的問題分流方式：關鍵字規則 vs 不同大小的地端模型。

用法（需要 Ollama 與 qwen2.5:0.5b／1.5b／3b）：
    python scripts/eval_router.py --out docs/router-experiments-raw.md

指標：
    5 類正確率      scores／docs／cause／chitchat／unanswerable 完全分對的比例
    行為正確率      只看實際處理方式是否正確（拒答／閒聊／完整流程）；scores、docs、cause 之間分錯不影響結果
    誤拒            可以回答的問題被分成 unanswerable（最嚴重：使用者拿不到答案）
    漏拒            unanswerable 沒被攔下（會讓模型對沒有的資料亂講）
    誤判閒聊        需要查資料的問題被分成 chitchat（不查資料就回答）
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

from app import router  # noqa: E402

ACTION = {"unanswerable": "拒答", "out_of_scope": "拒答", "crisis": "拒答", "chitchat": "閒聊", "course": "課程資料"}


def action(label: str) -> str:
    return ACTION.get(label, "完整流程")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(ROOT / "tests" / "router_eval.jsonl"))
    ap.add_argument("--models", nargs="*", default=["qwen2.5:0.5b", "qwen2.5:1.5b", "qwen2.5:3b"])
    ap.add_argument("--fields-models", nargs="*", default=["qwen2.5:1.5b", "qwen2.5:3b"])
    ap.add_argument("--out")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    cases = [json.loads(l) for l in open(args.file, encoding="utf-8") if l.strip()]

    methods = [("規則", "rule", None)] + [(m, "llm", m) for m in args.models]
    methods += [(f"{m} 欄位檢查", "fields", m) for m in args.fields_models]
    results = {}
    for name, mode, model in methods:
        if model:  # 暖機：第一次呼叫含載入模型的時間，不算進延遲
            router.route("你好", mode=mode, model=model)
        rows = []
        for c in cases:
            r = router.route(c["question"], mode=mode, model=model)
            rows.append(dict(c, pred=r.label, ms=r.ms, fallback="退回" in r.by))
        results[name] = rows
        print(f"done {name}", file=sys.stderr)

    n = len(cases)
    n_unans = sum(c["label"] == "unanswerable" for c in cases)
    n_ans = n - n_unans
    n_chat = sum(c["label"] == "chitchat" for c in cases)
    counts = "、".join(f"{lb} {sum(c['label'] == lb for c in cases)}" for lb in router.LABELS)
    out = [
        f"評測集 {n} 題（{counts}）。",
        "",
        "| 分類方式 | 5 類正確率 | 行為正確率 | 誤拒（可答→拒答） | 漏拒（應拒未拒） | 誤判閒聊 | 輸出不合法而退回規則 | 每題(ms) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, rows in results.items():
        acc = sum(r["pred"] == r["label"] for r in rows)
        act = sum(action(r["pred"]) == action(r["label"]) for r in rows)
        fr = sum(r["pred"] == "unanswerable" and r["label"] != "unanswerable" for r in rows)
        miss = sum(r["label"] == "unanswerable" and r["pred"] != "unanswerable" for r in rows)
        chat = sum(r["pred"] == "chitchat" and r["label"] != "chitchat" for r in rows)
        fb = sum(r["fallback"] for r in rows)
        ms = statistics.mean(r["ms"] for r in rows)
        out.append(
            f"| {name} | {acc}/{n} = {acc / n:.0%} | {act}/{n} = {act / n:.0%} | {fr}/{n_ans} | {miss}/{n_unans} | "
            f"{chat}/{n - n_chat} | {fb} | {ms:.0f} |"
        )
    out += ["", "分錯的題目（只列「行為」錯的；scores／docs／cause 互相分錯不列）", ""]
    out += ["| 題號 | 問題 | 正解 | " + " | ".join(results) + " |", "| --- " * (3 + len(results)) + "|"]
    for i, c in enumerate(cases):
        preds = [rows[i]["pred"] for rows in results.values()]
        if any(action(p) != action(c["label"]) for p in preds):
            cells = [p if action(p) == action(c["label"]) else f"**{p}**" for p in preds]
            out.append(f"| {c['id']} | {c['question']} | {c['label']} | " + " | ".join(cells) + " |")
    text = "\n".join(out)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
