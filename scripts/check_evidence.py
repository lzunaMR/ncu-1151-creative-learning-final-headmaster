"""逐筆重跑 /chat 回傳的 evidence.query，確認結果與 evidence.result 一致。

用法：
    python scripts/check_evidence.py tests/principal.jsonl            # 對執行中的 API 送題目
    python scripts/check_evidence.py tests/principal.jsonl --url http://localhost:8000
    python scripts/check_evidence.py tests/principal.jsonl --csv data/principal/scores.csv

需要 pandas。重跑方式與助教相同：df = pd.read_csv(scores.csv)，再 eval(query)。
"""

import argparse
import json
import math
import sys
import urllib.request
import uuid

import pandas as pd


def post_chat(url: str, session_id: str, message: str) -> dict:
    req = urllib.request.Request(
        f"{url}/chat",
        data=json.dumps({"session_id": session_id, "message": message}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def _py(v):
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _key(k):
    return "|".join(str(_py(x)) for x in k) if isinstance(k, tuple) else str(_py(k))


def to_jsonable(obj):
    if isinstance(obj, pd.DataFrame):
        return {_key(i): {_key(c): _py(v) for c, v in row.items()} for i, row in obj.iterrows()}
    if isinstance(obj, pd.Series):
        return {_key(i): _py(v) for i, v in obj.items()}
    return _py(obj)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--csv", default="data/principal/scores.csv")
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    cases = [json.loads(line) for line in open(args.file, encoding="utf-8") if line.strip()]
    total = bad = 0
    for case in cases:
        sid = f"{case['id']}-{uuid.uuid4().hex[:8]}"
        for message in case["turns"]:
            resp = post_chat(args.url, sid, message)
        for i, rec in enumerate(resp.get("recommendations") or []):
            for ev in rec.get("evidence") or []:
                total += 1
                got = to_jsonable(eval(ev["query"], {"df": df, "pd": pd}))
                ok = json.dumps(got, sort_keys=True) == json.dumps(ev["result"], sort_keys=True)
                bad += not ok
                print(f"{'OK  ' if ok else 'DIFF'}  {case['id']} rec[{i}]  {ev['query']}")
    print(f"\n{total - bad}/{total} 筆 evidence 重跑結果一致")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
