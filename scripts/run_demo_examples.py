"""把 tests/demo_examples.jsonl 的範例問題送進執行中的系統，產生附「實際輸出」的範例文件。只用標準函式庫。

用法（先啟動系統）：
    python scripts/run_demo_examples.py --out docs/demo-examples.md
    python scripts/run_demo_examples.py --ids docs-3 --label "版本 B"   # 只跑某幾題，例如切到版本 B 後重跑對照

輸出的每一題包含：問題、分類、要看什麼、實際回答（完整）、分流結果、檢索命中、護欄、建議與 evidence 數量、耗時。
模型的回答每次可能略有不同，數字與引用以「資料顯示的事實」與 Sources 為準。
"""

import argparse
import datetime
import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def chat(url: str, session: str, message: str) -> tuple[dict, float]:
    req = urllib.request.Request(
        f"{url}/chat",
        data=json.dumps({"session_id": session, "message": message}).encode(),
        headers={"Content-Type": "application/json"},
    )
    t = time.time()
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r), time.time() - t


def step(resp: dict, name: str) -> str:
    return " / ".join(t["detail"] for t in resp.get("trace", []) if t.get("step") == name)


def render(case: dict, resp: dict, secs: float, label: str) -> list[str]:
    g = resp["guardrail"]
    recs = resp.get("recommendations") or []
    title = f"### {case['id']}　{case['category']}" + (f"（{label}）" if label else "")
    lines = [title, ""]
    if len(case["turns"]) > 1:
        lines += ["多輪對話（同一個 session 依序送出）：", ""]
        lines += [f"{i}. {t}" for i, t in enumerate(case["turns"], 1)]
    else:
        lines += [f"**問：** {case['turns'][0]}"]
    lines += ["", f"**要看什麼：** {case['look_for']}"]
    if case.get("note"):
        lines += ["", f"> {case['note']}"]
    lines += [
        "",
        "| 項目 | 實際結果 |",
        "| --- | --- |",
        f"| 分流 | {step(resp, 'router') or '—'} |",
        f"| 護欄 | triggered={str(g['triggered']).lower()}、category={g['category']}、action={g['action']} |",
        f"| 檢索 | {(step(resp, 'retrieve') or '—').replace('|', '／')} |",
        f"| Sources | {', '.join(s['id'] for s in resp['sources']) or '（無）'} |",
        f"| 建議與統計依據 | {len(recs)} 項，每項 evidence 數：{', '.join(str(len(r['evidence'])) for r in recs) or '—'} |",
        f"| 生成 | {step(resp, 'generate') or '不生成'} |",
        f"| 查核 | 引用：{step(resp, 'citation_check') or '—'}；數字：{step(resp, 'number_check') or '—'} |",
        f"| 耗時 | {secs:.0f} 秒（route={resp['route']}） |",
        "",
        "<details><summary>實際回答</summary>",
        "",
        "```text",
        resp["answer"].strip(),
        "```",
        "",
        "</details>",
        "",
    ]
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--file", default=str(ROOT / "tests" / "demo_examples.jsonl"))
    ap.add_argument("--ids", nargs="*", help="只跑這幾題")
    ap.add_argument("--label", default="", help="標在題目後面，例如「版本 B」")
    ap.add_argument("--out", help="寫成 Markdown 檔")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")

    cases = [json.loads(l) for l in open(args.file, encoding="utf-8") if l.strip()]
    if args.ids:
        cases = [c for c in cases if c["id"] in args.ids]
    with urllib.request.urlopen(f"{args.url}/health", timeout=10) as r:
        health = json.load(r)

    body, current = [], None
    for case in cases:
        session = f"demo-{uuid.uuid4().hex[:8]}"
        total = 0.0
        for msg in case["turns"]:
            resp, secs = chat(args.url, session, msg)
            total += secs
        top = case["category"].split("（")[0]
        if top != current:
            body += [f"## {top}", ""]
            current = top
        body += render(case, resp, total, args.label)
        print(f"done {case['id']}（{total:.0f} 秒）", file=sys.stderr)

    models = ", ".join((health.get("ollama") or {}).get("models", []))
    head = [
        "# 詢問範例與實際輸出",
        "",
        f"> 由 `scripts/run_demo_examples.py` 對執行中的系統自動產生（{datetime.date.today()}；模型：{models}）。",
        "> 題目定義在 `tests/demo_examples.jsonl`。模型的「解讀與建議」每次可能略有不同；數字與引用以「資料顯示的事實」與 Sources 為準。",
        "> 系統改版後重跑：`python scripts/run_demo_examples.py --out docs/demo-examples.md`",
        "",
    ]
    text = "\n".join(head + body)
    if args.out:
        Path(args.out).write_text(text.rstrip() + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
