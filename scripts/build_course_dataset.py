"""從 Python 課平台的資料庫備份（pg_dump 純文字 .sql）產生可公開的彙總資料集。

用法：
    python scripts/build_course_dataset.py <dump.sql> [--out data/course/python]

只輸出彙總統計，不輸出任何個人層級的資料：
    problems.md            練習題題目（說明、輸入輸出格式、提示）；不含參考解答、測資、範例
    problem_stats.csv      每題作答人數、通過人數、通過率、提交次數、錯誤類型比例
    grade_item_stats.csv   每個成績項目的人數、平均、中位數、四分位數、標準差、分數區間人數
去識別化規則：
    - 只計入該課程的成員（course_members），排除教師帳號與非成員
    - 不輸出姓名、學號、email、密碼、時間戳記、程式碼、對話內容
    - 排除考試題（exam_mode 或 is_hidden），避免洩漏考題
    - 小樣本抑制：人數 1～MIN_CELL-1 的格子不輸出，而且要防止「用總數相減」反推——
      分數區間只有在每個非零區間都 >= MIN_CELL 時才輸出；題目的通過人數只有在通過與未通過
      都是 0 或 >= MIN_CELL 時才輸出；分布不能公開時，平均與標準差也不輸出（0/100 計分時平均會透露人數）。
      人數一律四捨五入到 5 的倍數（各區間加總因此不一定等於總人數）；平均與標準差取整數、通過率取到小數第 2 位，
      避免由平均反推出確切人數。也不輸出最高分、最低分
    - 排除課程成員都沒有作答的題目，以及 FIN／FINAL 開頭的題目（期末考）
備份檔本身不可 commit（已列入 .gitignore）。
"""

import argparse
import collections
import csv
import html
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIN_CELL = 5
COURSE_ID = "1"
NULL = "\\N"
BUCKETS = [("0-59", 0, 60), ("60-69", 60, 70), ("70-79", 70, 80), ("80-89", 80, 90), ("90-100", 90, 101)]
TABLES = {
    "debugging.problem",
    "debugging.debugging_code_submission",
    "debugging.grade_items",
    "debugging.grade_scores",
    "debugging.user_info",
    "debugging.course_members",
}


def load(path: Path) -> dict[str, list[dict]]:
    """只讀需要的表；pg_dump 的 COPY 區塊為 tab 分隔，\\N 代表 NULL。"""
    data, cols, cur = collections.defaultdict(list), {}, None
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("COPY "):
                t = line.split()[1]
                if t in TABLES:
                    cur = t
                    cols[t] = [c.strip().strip('"') for c in line[line.index("(") + 1 : line.index(")")].split(",")]
                continue
            if cur:
                if line.startswith("\\."):
                    cur = None
                    continue
                data[cur].append(dict(zip(cols[cur], line.rstrip("\n").split("\t"))))
    return data


def unescape(s: str) -> str:
    """COPY 文字格式的跳脫：\\n、\\t、\\\\。"""
    if s == NULL:
        return ""
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            out.append({"n": "\n", "t": "\t", "r": "", "\\": "\\"}.get(s[i + 1], s[i + 1]))
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def result_name(raw: str) -> str:
    """result 欄位存的是 JSON 字串，例如 "Accepted"。"""
    s = unescape(raw)
    try:
        s = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        pass
    return str(s).strip('"')


def clean(s: str) -> str:
    """平台存的是 HTML：<br>、</p> 換成換行，其餘標籤去掉，再還原 &nbsp; 等實體。"""
    s = re.sub(r"(?i)<br\s*/?>|</p>", "\n", unescape(s))
    s = html.unescape(re.sub(r"</?[A-Za-z][^>]*>", "", s)).replace("\xa0", " ")
    return re.sub(r"\n{2,}", "\n", "\n".join(l.strip() for l in s.splitlines())).strip()


def natural(s: str) -> list:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def round5(n: int) -> int:
    """人數四捨五入到 5 的倍數，避免用「成員總數 − 作答人數」反推少數人。"""
    return int(5 * round(n / 5))


def suppress(n: int) -> int | str:
    return n if n == 0 or n >= MIN_CELL else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--out", default=str(ROOT / "data" / "course" / "python"))
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    d = load(Path(args.dump))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    teachers = {r["stu_id"] for r in d["debugging.user_info"] if r["is_teacher"] == "t"}
    members = {r["student_id"] for r in d["debugging.course_members"] if r["course_id"] == COURSE_ID} - teachers

    # ---- 題目（排除考試題）
    attempted = {s["problem_id"] for s in d["debugging.debugging_code_submission"] if s["student_id"] in members}
    problems = sorted(
        (
            p
            for p in d["debugging.problem"]
            if p["is_hidden"] != "t"
            and p["exam_mode"] != "t"
            and p["problem_id"] in attempted
            and not re.match(r"(?i)fin", p["problem_id"])
            and unescape(p["title"]).strip()
        ),
        key=lambda p: natural(p["problem_id"]),
    )
    # 題號沿用平台的 problem_id（例如 C3_A2 → PY-C3-A2），方便管理者對照
    pid_name = {p["problem_id"]: "PY-" + p["problem_id"].replace("_", "-") for p in problems}
    lines = [
        "# PY Python 程式設計課程練習題",
        "",
        "> 由 scripts/build_course_dataset.py 從課程平台產生；只含練習題的題目說明，不含參考解答、測資與考試題。",
        "",
    ]
    for p in problems:
        lines += [f"### {pid_name[p['problem_id']]} {clean(p['title'])}", ""]
        for label, key in (("題目", "description"), ("輸入", "input_description"), ("輸出", "output_description"), ("提示", "hint")):
            text = clean(p[key])
            if text:
                lines += [f"{label}：{text}", ""]
    (out / "problems.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    # ---- 每題統計（只算課程成員）
    by_prob = collections.defaultdict(lambda: collections.defaultdict(list))
    for s in d["debugging.debugging_code_submission"]:
        if s["student_id"] in members and s["problem_id"] in pid_name:
            by_prob[s["problem_id"]][s["student_id"]].append(result_name(s["result"]))
    rows = []
    for p in problems:
        per_student = by_prob.get(p["problem_id"], {})
        n_try = len(per_student)
        n_pass = sum("Accepted" in rs for rs in per_student.values())
        subs = [r for rs in per_student.values() for r in rs]
        fails = collections.Counter(r for r in subs if r != "Accepted")
        ok = n_try >= MIN_CELL
        n_fail = n_try - n_pass
        safe = ok and (n_pass == 0 or n_pass >= MIN_CELL) and (n_fail == 0 or n_fail >= MIN_CELL)
        rows.append(
            {
                "problem_id": pid_name[p["problem_id"]],
                "title": clean(p["title"]),
                "students_attempted": round5(n_try) if ok else "",
                "students_passed": round5(n_pass) if safe else "",
                "pass_rate": round(n_pass / n_try, 2) if safe else "",
                # 以下以「提交次數」為單位，不是人數
                "accepted_share": round(sum(r == "Accepted" for r in subs) / len(subs), 3) if ok else "",
                "submissions": len(subs) if ok else "",
                "median_submissions_per_student": statistics.median(len(rs) for rs in per_student.values()) if ok else "",
                "wrong_answer_share": round(fails["Wrong Answer"] / len(subs), 3) if ok else "",
                "runtime_error_share": round(fails["Runtime Error"] / len(subs), 3) if ok else "",
                "time_limit_share": round(fails["Time Limit Exceeded"] / len(subs), 3) if ok else "",
            }
        )
    with open(out / "problem_stats.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # ---- 成績項目統計
    items = sorted((i for i in d["debugging.grade_items"] if i["course_id"] == COURSE_ID), key=lambda i: int(i["position"]) if i["position"] != NULL else int(i["id"]))
    scores = collections.defaultdict(list)
    for s in d["debugging.grade_scores"]:
        if s["student_id"] in members and s["score"] != NULL:
            scores[s["item_id"]].append(float(s["score"]))
    grows = []
    for it in items:
        xs = scores.get(it["id"], [])
        ok = len(xs) >= MIN_CELL
        q = statistics.quantiles(xs, n=4) if ok else ["", "", ""]
        row = {
            "item": clean(it["name"]),
            "max_score": float(it["max_score"]),
            "students": round5(len(xs)) if ok else "",
            "mean": round(statistics.mean(xs)) if ok else "",
            "median": round(statistics.median(xs), 2) if ok else "",
            "p25": round(q[0], 2) if ok else "",
            "p75": round(q[2], 2) if ok else "",
            "std": round(statistics.stdev(xs)) if ok else "",
        }
        counts = [sum(lo <= x < hi for x in xs) for _, lo, hi in BUCKETS]
        show = ok and all(c == 0 or c >= MIN_CELL for c in counts)
        row["mean"] = row["mean"] if show else ""
        row["std"] = row["std"] if show else ""
        for (name, _, _), c in zip(BUCKETS, counts):
            row[f"n_{name}"] = round5(c) if show else ""
        grows.append(row)
    with open(out / "grade_item_stats.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(grows[0]))
        w.writeheader()
        w.writerows(grows)

    print(f"課程成員 {len(members)} 人；收錄練習題 {len(problems)} 題（平台共 {len(d['debugging.problem'])} 題）；成績項目 {len(items)} 個")
    print(f"輸出到 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
