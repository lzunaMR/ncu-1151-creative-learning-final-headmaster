"""評估「範圍判斷」：校長題不可被拒答；別組的問題要拒答；危機訊息要給求助專線。只看規則，不需要模型。

用法：
    python scripts/eval_scope.py
    ENABLE_COURSE=1 python scripts/eval_scope.py      # 課程資料開啟時（課程詞較多，誤判風險較高）

資料：
    校長題（不可被拒答）：principal.jsonl 的 pattern／open／cause、兩份檢索評測集、分流評測集與兩份保留集中
                          scores／docs／cause／course 類、範圍保留集的 principal 類
    別組的問題：tutor、teacher、parent 公開題。單號題（001、003…）是寫規則時參考的「開發」，雙號題只用來評分
    範圍保留集 tests/scope_holdout.jsonl：寫規則之前就 commit（4e0ab1b），完全沒參考
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

from app import router  # noqa: E402

REFUSE = {"unanswerable", "out_of_scope", "crisis"}


def has_hotline(msg: str, label: str) -> bool:
    reply = {"crisis": router.CRISIS_REPLY, "out_of_scope": router.out_of_scope_reply(msg)}.get(label, "")
    return "1925" in reply


def rows(name: str) -> list[dict]:
    return [json.loads(l) for l in open(ROOT / "tests" / name, encoding="utf-8") if l.strip()]


def last(c: dict) -> str:
    return c["turns"][-1] if "turns" in c else c["question"]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    print(f"ENABLE_COURSE={os.environ.get('ENABLE_COURSE', '0')}\n")

    # ---- 校長題：不可被拒答
    principal = [c for c in rows("principal.jsonl") if c["tier"] != "unanswerable"]
    principal += rows("retrieval_eval.jsonl") + rows("retrieval_eval_hard.jsonl")
    for f in ("router_eval.jsonl", "router_eval_holdout.jsonl", "router_eval_holdout2.jsonl"):
        principal += [c for c in rows(f) if c["label"] in ("scores", "docs", "cause", "course")]
    principal += [c for c in rows("scope_holdout.jsonl") if c["label"] == "principal"]
    wrong = [(last(c), router.classify_rule(last(c))) for c in principal if router.classify_rule(last(c)) in REFUSE]
    print(f"校長題 {len(principal)} 題，被誤拒 {len(wrong)} 題")
    for q, p in wrong:
        print(f"   ✗ {q} → {p}")

    # ---- 別組公開題
    def report(name, cases, ok, desc):
        good = [c for c in cases if ok(c, router.classify_rule(last(c)))]
        print(f"{name}：{len(good)}/{len(cases)} {desc}")
        for c in cases:
            if c not in good:
                print(f"   ✗ {c['id']} {last(c)} → {router.classify_rule(last(c))}")

    for split, keep in (("開發（單號，寫規則時參考）", 1), ("評分（雙號，沒參考）", 0)):
        print(f"\n== 別組公開題：{split}")
        pick = lambda f: [c for c in rows(f) if int(c["id"].split("-")[1]) % 2 == keep]  # noqa: E731
        report("家教組（解題、概念）", pick("tutor.jsonl"), lambda c, p: p in REFUSE, "拒答")
        report("助教組（出題、摘要）", pick("teacher.jsonl"), lambda c, p: p in REFUSE, "拒答")
        parent = pick("parent.jsonl")
        report("家長組 explicit（危機）", [c for c in parent if c["tier"] == "explicit"], lambda c, p: p == "crisis", "判斷為危機")
        report("家長組 explicit＋ambiguous", [c for c in parent if c["tier"] in ("explicit", "ambiguous")],
               lambda c, p: has_hotline(last(c), p), "回覆含求助專線")
        report("家長組 ambiguous", [c for c in parent if c["tier"] == "ambiguous"], lambda c, p: p in REFUSE, "拒答（危機或範圍外皆可）")
        normal = [c for c in parent if c["tier"] == "normal"]
        n_ref = sum(router.classify_rule(last(c)) in REFUSE for c in normal)
        print(f"家長組 normal：{n_ref}/{len(normal)} 拒答，其餘交給一般流程（不硬性要求；一般流程不保證會說不知道）")

    # ---- 範圍保留集
    print("\n== 範圍保留集（寫規則前就 commit，完全沒參考）")
    hold = rows("scope_holdout.jsonl")
    report("非校務問題", [c for c in hold if c["label"] == "out_of_scope"], lambda c, p: p in REFUSE, "拒答")
    report("危機訊息：判斷為危機", [c for c in hold if c["label"] == "crisis"], lambda c, p: p == "crisis", "")
    report("危機訊息：回覆含求助專線（看過上一行的結果才加的保護，不是乾淨成績）",
           [c for c in hold if c["label"] == "crisis"], lambda c, p: has_hotline(last(c), p), "")
    report("含風險用詞的校長題", [c for c in hold if c["label"] == "principal"], lambda c, p: p not in REFUSE, "沒被拒答")
    return 0 if not wrong else 1


if __name__ == "__main__":
    sys.exit(main())
