"""比較學科知識題的四種把關方法（api/app/knowledge.py）：擋下多少學科知識題、會不會誤擋校長題。

用法（需要 Ollama；在本機跑完整流程，不經過 Docker）：
    python scripts/eval_knowledge.py --methods off intent prompt llm rule --out docs/knowledge-guard-raw.md
    python scripts/eval_knowledge.py --methods intent --holdout       # 選定方法後，才跑保留集（只跑一次）

選擇規則（跑實驗前就定好，寫在這裡）：
    1. 校長題不可以比 off（目前版本）多出任何一題「拒答或說不知道」。這是最嚴重的錯誤：校長拿不到答案。
    2. 通過 1 的方法中，選開發集學科知識題擋下最多的。
    3. 還是平手，選比較快的（每題多花的時間）。
    保留集 tests/knowledge_holdout.jsonl 只在選定之後跑，不拿來選方法。

修訂（第一輪之後、跑保留集之前）：第一輪每題只跑一次。依上面的規則會選 intent,prompt，但重跑 3 次發現它在
「英文的現在完成式怎麼用？」3 次都用自己的知識回答，單跑一次的結果不可靠。因此改成每題跑 --repeat 次：
    1'. 某題校長題在所有重跑中都沒回答、而 off 至少回答過一次 → 算誤擋；誤擋 0 題才合格。
    2'. 學科知識題以人工逐題閱讀所有重跑的回答，算「用自己的知識回答」的次數，越少越好。

判斷「沒有回答」：護欄拒答、固定的「不知道」回覆，或「解讀與建議」整段只有一句不知道／不在範圍（40 字以內）。
第一次跑時用「回答中出現不知道」判斷，結果模型常在回答後面加一句「資料中沒有提到，我不知道其他…」，誤判很多，才改成這個。
學科知識題還是要人工逐題讀：「我不知道。不過文言文可以這樣讀…」這種先說不知道、再用自己的知識回答的情況，字串比對分不出來。
生成的 temperature 是 0.3，同一題每次回答可能不同；單跑一次的差異 1～2 題可能只是隨機，見 docs/knowledge-guard.md。
"""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("PRINCIPAL_SCORES_CSV", str(ROOT / "data" / "principal" / "scores.csv"))

from app import pipeline, router  # noqa: E402

# 開發集：寫方法時參考。前兩題是上一版（範圍判斷）沒擋下、已經看過的題目
DEV_KNOWLEDGE = [
    "英文的現在完成式怎麼用？",
    "學生解方程式時常犯哪些錯？",
    "數學的因式分解怎麼做？",
    "自然科的牛頓第一定律是什麼？",
    "國文的文言文要怎麼讀懂？",
    "英文的關係代名詞怎麼用？",
    "比與比例式要怎麼算？",
    "晨讀要選什麼書比較好？",
]
NOT_ANSWERED = ("不知道", "不在範圍", "學科知識的問題")
FIXED_REPLIES = ("不在校長 AI 決策助手的範圍", "找不到可以回答這個問題的內容", "這個問題需要的資訊不在本系統")


def not_answered(answer: str, refused: bool) -> bool:
    body = answer.split("【解讀與建議】（含推測，非資料直接證實）")[-1]
    body = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("⚠")).strip()  # 查核附上的警告不算回答內容
    return refused or any(w in answer for w in FIXED_REPLIES) or (len(body) <= 40 and any(w in body for w in NOT_ANSWERED))


def rows(name: str) -> list[dict]:
    return [json.loads(l) for l in open(ROOT / "tests" / name, encoding="utf-8") if l.strip()]


def last(c: dict) -> str:
    return c["turns"][-1] if "turns" in c else c["question"]


def principal_questions() -> list[str]:
    """與 scripts/eval_scope.py 相同的校長題（去重；課程資料關閉時不含課程題）。"""
    qs = [last(c) for c in rows("principal.jsonl") if c["tier"] != "unanswerable"]
    qs += [last(c) for c in rows("retrieval_eval.jsonl") + rows("retrieval_eval_hard.jsonl")]
    for f in ("router_eval.jsonl", "router_eval_holdout.jsonl", "router_eval_holdout2.jsonl"):
        # 課程題只在課程資料開啟時算（關閉時回答不知道是對的）
        keep = ("scores", "docs", "cause", "course") if router.COURSE_ENABLED else ("scores", "docs", "cause")
        qs += [last(c) for c in rows(f) if c["label"] in keep]
    qs += [last(c) for c in rows("scope_holdout.jsonl") if c["label"] == "principal"]
    return list(dict.fromkeys(qs))


def ask(q: str, n: int = 1) -> dict:
    runs = [ask_once(q) for _ in range(n)]
    return {**runs[0], "secs": sum(r["secs"] for r in runs) / n, "not_answered": all(r["not_answered"] for r in runs),
            "n_not": sum(r["not_answered"] for r in runs), "runs": runs}


def ask_once(q: str) -> dict:
    t = time.perf_counter()
    r = pipeline.handle(f"eval-{uuid.uuid4()}", q)
    return {
        "q": q, "secs": time.perf_counter() - t, "answer": r.answer, "refuse": r.guardrail.action == "refuse",
        "not_answered": not_answered(r.answer, r.guardrail.action == "refuse"),
        "trace": [f"{s.step}: {s.detail}" for s in r.trace],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", nargs="*", default=["off", "intent", "prompt", "llm", "rule"])
    ap.add_argument("--holdout", action="store_true", help="改跑保留集（選定方法後才用）")
    ap.add_argument("--repeat", type=int, default=1, help="每題重跑幾次（生成有隨機性）")
    ap.add_argument("--out")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")

    if args.holdout:
        hold = rows("knowledge_holdout.jsonl")
        sets = {"knowledge": [c["question"] for c in hold if c["label"] == "knowledge"],
                "principal": [c["question"] for c in hold if c["label"] == "principal"]}
    else:
        sets = {"knowledge": DEV_KNOWLEDGE, "principal": principal_questions()}
    print(f"學科知識題 {len(sets['knowledge'])} 題，校長題 {len(sets['principal'])} 題", flush=True)

    pipeline.handle("warmup", "最近一學期（1142）哪個年級的數學平均表現下滑最多？")
    results = {}
    for m in args.methods:
        os.environ["KNOWLEDGE_GUARD"] = m
        results[m] = {k: [ask(q, args.repeat) for q in qs] for k, qs in sets.items()}
        k, p = results[m]["knowledge"], results[m]["principal"]
        secs = sum(x["secs"] for x in k + p) / len(k + p)
        print(f"{m:8s} 學科知識題每次都擋下 {sum(x['not_answered'] for x in k)}/{len(k)}；"
              f"校長題沒回答 {sum(x['not_answered'] for x in p)}/{len(p)}（拒答 {sum(x['refuse'] for x in p)}）；每題 {secs:.1f} 秒", flush=True)

    lines = ["# 學科知識題把關：實驗原始輸出", "", f"資料：{'保留集' if args.holdout else '開發集＋校長題'}", ""]
    base = results.get("off")
    lines += ["| 方法 | 學科知識題擋下 | 校長題沒回答 | 比 off 多出的校長題 | 每題秒數 |", "| --- | --- | --- | --- | --- |"]
    for m, r in results.items():
        k, p = r["knowledge"], r["principal"]
        extra = [x["q"] for i, x in enumerate(p) if x["not_answered"] and base and base["principal"][i]["n_not"] < args.repeat]
        secs = sum(x["secs"] for x in k + p) / len(k + p)
        lines.append(f"| {m} | {sum(x['not_answered'] for x in k)}/{len(k)} | {sum(x['not_answered'] for x in p)}/{len(p)} | "
                     f"{len(extra) if base else '—'} | {secs:.1f} |")
        print(f"{m}：比 off 多出沒回答的校長題：{extra if base else '（沒跑 off，無法比較）'}", flush=True)
    for m, r in results.items():
        lines += ["", f"## {m}"]
        for kind, xs in r.items():
            for x in xs:
                if kind == "principal" and not x["n_not"]:
                    continue  # 校長題只列至少一次沒回答的；全部列出太長
                for j, run in enumerate(x["runs"]):
                    mark = "沒回答" if run["not_answered"] else "有回答"
                    lines += ["", f"### [{kind}] {x['q']}　（第 {j + 1} 次）→ {mark}", "", "```text", run["answer"], "```",
                              "", "trace：" + "｜".join(t for t in run["trace"] if t.split(":")[0] in ("router", "knowledge", "guardrail", "honesty"))]
    if args.out:
        Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
