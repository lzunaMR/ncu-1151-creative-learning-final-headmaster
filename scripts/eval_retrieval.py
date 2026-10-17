"""對自訂評測集跑兩個版本的檢索，輸出命中紀錄與命中率（Markdown）。只用標準函式庫。

用法：
    python scripts/eval_retrieval.py                        # 版本 A、B，K=5
    python scripts/eval_retrieval.py --k 3 --out hits.md
    python scripts/eval_retrieval.py --mock                 # retrieval.py 完成前，用內建假檢索器自測

「命中」的定義：Top-K 的段落 id 含任一應命中 id。
輸出的表格欄位與 docs/midterm-report.md §4 相同，可直接貼上。
評測集格式（每行一題）：{"id": ..., "question": ..., "expect_ids": [...]}
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSIONS = ("A", "B")
HEADING = re.compile(r"^###\s+(\S+)\s+(.*)$")


def load_paragraphs(docs_dir: Path) -> dict[str, str]:
    """段落 id → 原文（含標題文字）。"""
    paras: dict[str, list[str]] = {}
    for path in sorted(docs_dir.glob("*.md")):
        current = None
        for line in path.read_text(encoding="utf-8").splitlines():
            m = HEADING.match(line)
            if m:
                current = m.group(1)
                paras[current] = [m.group(2)]
            elif line.startswith("#"):
                current = None
            elif current and line.strip():
                paras[current].append(line.strip())
    return {pid: "\n".join(lines) for pid, lines in paras.items()}


def load_retriever():
    """匯入 api/app/retrieval.py 的 retrieve(query, k, version)。"""
    sys.path.insert(0, str(ROOT / "api"))
    from app.retrieval import retrieve

    return retrieve


def ngrams(text: str, n: int) -> set[str]:
    text = re.sub(r"\s+", "", text)
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def make_mock(paras: dict[str, str]):
    """假檢索器：版本 A 比單字重疊、版本 B 比雙字重疊。只用來測腳本本身。"""

    def retrieve(query: str, k: int, version: str) -> list[dict]:
        n = 1 if version == "A" else 2
        q = ngrams(query, n)
        ranked = sorted(paras, key=lambda pid: -len(q & ngrams(paras[pid], n)))
        return [{"id": pid, "text": paras[pid]} for pid in ranked[:k]]

    return retrieve


def top_ids(retrieve, question: str, k: int, version: str) -> list[str]:
    """一個切塊涵蓋多段時 sources 會展開成多筆，這裡依序去重。"""
    ids: list[str] = []
    for s in retrieve(question, k, version):
        sid = s["id"] if isinstance(s, dict) else s.id
        if sid not in ids:
            ids.append(sid)
    return ids


def render(cases: list[dict], results: dict[str, list[list[str]]], k: int) -> str:
    lines = [
        f"命中定義：Top-K 的段落 id 含任一應命中 id；K = {k}。",
        "",
        "| # | 問題 | 應命中 | 版本 A 實際命中（Top-K） | A 命中？ | 版本 B 實際命中（Top-K） | B 命中？ |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    hits = {v: 0 for v in VERSIONS}
    for i, case in enumerate(cases):
        row = [str(i + 1), case["question"], ", ".join(case["expect_ids"])]
        for v in VERSIONS:
            got = results[v][i]
            hit = bool(set(got) & set(case["expect_ids"]))
            hits[v] += hit
            row += [", ".join(got), "是" if hit else "否"]
        lines.append("| " + " | ".join(row) + " |")

    n = len(cases)
    rate = {v: f"{hits[v]}/{n} = {hits[v] / n:.0%}" for v in VERSIONS}
    lines += [
        "",
        "| | 版本 A | 版本 B |",
        "| --- | --- | --- |",
        f"| 命中率 | {rate['A']} | {rate['B']} |",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(ROOT / "tests" / "retrieval_eval.jsonl"))
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--docs", default=str(ROOT / "data" / "principal" / "docs"))
    ap.add_argument("--out", help="另存成 UTF-8 檔案")
    ap.add_argument("--mock", action="store_true", help="用內建假檢索器，結果不可寫進報告")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")

    with open(args.file, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f if line.strip()]
    if not cases:
        print("評測集是空的", file=sys.stderr)
        return 2

    paras = load_paragraphs(Path(args.docs))
    unknown = [(c["id"], pid) for c in cases for pid in c["expect_ids"] if pid not in paras]
    if unknown:
        for cid, pid in unknown:
            print(f"{cid} 的應命中 id {pid} 不在 {args.docs}", file=sys.stderr)
        return 2

    if args.mock:
        retrieve = make_mock(paras)
    else:
        try:
            retrieve = load_retriever()
        except ImportError as e:
            print(f"無法匯入 app.retrieval.retrieve：{e}\n要先測腳本本身請加 --mock", file=sys.stderr)
            return 2

    results = {v: [top_ids(retrieve, c["question"], args.k, v) for c in cases] for v in VERSIONS}
    text = render(cases, results, args.k)
    if args.mock:
        text = "> 假檢索器（--mock）的輸出，不是系統的實際結果，不可寫進報告。\n\n" + text

    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
