"""跑 jsonl 測試題並統計結果。只用標準函式庫，不需安裝套件。

用法：
    python scripts/run_tests.py tests/my_tests.jsonl
    python scripts/run_tests.py tests/parent.jsonl --url http://localhost:8000

測試題格式見 spec/acceptance.md。
"""

import argparse
import json
import sys
import urllib.request
import uuid
from collections import defaultdict

REQUIRED_FIELDS = ("answer", "route", "guardrail", "sources", "trace")


def post_chat(url: str, session_id: str, message: str) -> dict:
    req = urllib.request.Request(
        f"{url}/chat",
        data=json.dumps({"session_id": session_id, "message": message}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def get_path(obj, dotted: str):
    for key in dotted.split("."):
        if not isinstance(obj, dict) or key not in obj:
            raise KeyError(dotted)
        obj = obj[key]
    return obj


def check(resp: dict, expect: dict) -> list[str]:
    """回傳不符合的項目；空 list 代表通過。"""
    errors = [f"缺少欄位 {f}" for f in REQUIRED_FIELDS if f not in resp]
    if errors:
        return errors

    for key, want in expect.items():
        if key == "answer_not_contains":
            errors += [f"answer 不應包含「{s}」" for s in want if s in resp["answer"]]
        elif key == "answer_contains_any":
            if not any(s in resp["answer"] for s in want):
                errors.append(f"answer 應包含其中之一：{want}")
        elif key == "sources_include":
            ids = {s.get("id") for s in resp["sources"]}
            if not ids & set(want):
                errors.append(f"sources 應包含其中之一：{want}，實際為 {sorted(ids)}")
        elif key == "questions_count":
            got = len(resp.get("questions") or [])
            if got != want:
                errors.append(f"questions 應有 {want} 題，實際為 {got} 題")
        elif key == "recommendations_min":
            recs = resp.get("recommendations") or []
            if len(recs) < want:
                errors.append(f"recommendations 至少要 {want} 項，實際為 {len(recs)} 項")
            errors += [f"recommendations[{i}] 沒有 evidence" for i, r in enumerate(recs) if not r.get("evidence")]
        else:
            try:
                got = get_path(resp, key)
            except KeyError:
                errors.append(f"缺少欄位 {key}")
                continue
            if got != want:
                errors.append(f"{key} 應為 {want!r}，實際為 {got!r}")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--url", default="http://localhost:8000")
    args = ap.parse_args()

    with open(args.file, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f if line.strip()]

    by_tier: dict[str, list[bool]] = defaultdict(list)
    for case in cases:
        session_id = f"{case['id']}-{uuid.uuid4().hex[:8]}"
        try:
            for message in case["turns"]:
                resp = post_chat(args.url, session_id, message)
            errors = check(resp, case.get("expect", {}))
        except Exception as e:  # 連線失敗、回傳非 JSON 等都算失敗
            errors = [f"{type(e).__name__}: {e}"]

        ok = not errors
        by_tier[case.get("tier", "-")].append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {case['id']}")
        for err in errors:
            print(f"      {err}")

    print("\n分層統計")
    for tier, results in by_tier.items():
        print(f"  {tier:<14} {sum(results)}/{len(results)}")
    total = [r for rs in by_tier.values() for r in rs]
    print(f"  {'總計':<12} {sum(total)}/{len(total)}")
    return 0 if all(total) else 1


if __name__ == "__main__":
    sys.exit(main())
