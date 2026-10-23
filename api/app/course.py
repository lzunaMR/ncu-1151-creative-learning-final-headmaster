"""Python 課程彙總資料（data/course/python/）：題目檢索＋統計查詢。

問題分流判定為 course 時由 pipeline 呼叫。資料只有彙總統計，沒有任何個人層級的資料；
人數已四捨五入到 5 的倍數，所以回覆只比較相對高低，不報精確人數。
evidence 的 query 可用下列方式重跑：
    ps = pd.read_csv("data/course/python/problem_stats.csv")
    gs = pd.read_csv("data/course/python/grade_item_stats.csv")
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import analytics, retrieval
from .schemas import Evidence, Recommendation, Source, TraceStep

COURSE_DIR = Path(os.environ.get("COURSE_DIR", "/data/course/python").strip())
if not COURSE_DIR.exists():  # 不在 Docker 內時，改用 repo 內的資料
    COURSE_DIR = Path(__file__).resolve().parents[2] / "data" / "course" / "python"
HEADING_RE = re.compile(r"^###\s+(PY-\S+)\s*(.*)$")


# ---------------------------------------------------------------- 題目檢索


def load_problems() -> dict[str, str]:
    """題號 → 題目全文（含標題）。"""
    out: dict[str, list[str]] = {}
    cur = None
    for line in (COURSE_DIR / "problems.md").read_text(encoding="utf-8").splitlines():
        m = HEADING_RE.match(line)
        if m:
            cur = m.group(1)
            out[cur] = [m.group(2)]
        elif cur and line.strip() and not line.startswith(("#", ">")):
            out[cur].append(line.strip())
    return {pid: "\n".join(lines) for pid, lines in out.items()}


_index: tuple[list[str], list[list[float]], retrieval.BM25] | None = None


def search_problems(query: str, k: int = 3) -> list[Source]:
    """一題一塊，向量＋BM25（字元二元組）以 RRF 合併，也就是校務文件的版本 B。

    題目說明充滿「切片」「while」「字典」「函式」這類技術詞，使用者也用同樣的詞問，
    這時 BM25 的字面比對有幫助（校務文件的難題集則相反）。"""
    global _index
    probs = load_problems()
    if _index is None:
        ids = list(probs)
        texts = [probs[i] for i in ids]
        _index = (ids, retrieval.embed(texts, "bge-m3"), retrieval.BM25([retrieval.tokenize(t) for t in texts]))
    ids, vecs, bm25 = _index
    q = retrieval.embed([query], "bge-m3")[0]
    n = range(len(ids))
    dense = sorted(n, key=lambda i: -retrieval._dot(q, vecs[i]))
    sparse_scores = bm25.scores(retrieval.tokenize(query))
    sparse = sorted(n, key=lambda i: -sparse_scores[i])
    fused = retrieval.rrf([dense, sparse])
    ranked = sorted(n, key=lambda i: -fused[i])
    return [Source(id=ids[i], text=probs[ids[i]]) for i in ranked[:k]]


# ---------------------------------------------------------------- 統計查詢


def _frames() -> dict[str, pd.DataFrame]:
    return {
        "ps": pd.read_csv(COURSE_DIR / "problem_stats.csv"),
        "gs": pd.read_csv(COURSE_DIR / "grade_item_stats.csv"),
    }


def run_query(query: str) -> Evidence:
    """在 ps（每題統計）、gs（成績項目統計）上執行一段 pandas 運算式。"""
    result = eval(query, {"pd": pd}, _frames())  # noqa: S307  只執行本模組產生的固定查詢
    return Evidence(query=query, result=analytics.to_jsonable(result))


# (關鍵字, 說明, 查詢, 結論的寫法)
QUERIES = [
    (
        r"超時|time ?limit|太慢|效率",
        "超時比例最高的題目",
        "ps.dropna(subset=['time_limit_share']).sort_values('time_limit_share', ascending=False)"
        ".head(3)[['problem_id', 'title', 'time_limit_share', 'submissions']]",
        "提交中超時（Time Limit Exceeded）比例最高的是 {top}",
    ),
    (
        r"執行錯誤|runtime|當掉|錯誤訊息|語法",
        "執行錯誤比例最高的題目",
        "ps.dropna(subset=['runtime_error_share']).sort_values('runtime_error_share', ascending=False)"
        ".head(3)[['problem_id', 'title', 'runtime_error_share', 'submissions']]",
        "提交中執行錯誤（Runtime Error）比例最高的是 {top}",
    ),
    (
        r"隨堂考|小考",
        "各次隨堂考的平均與中位數",
        "gs[gs.item.str.contains('隨堂考')][['item', 'students', 'mean', 'median', 'p25', 'p75']]",
        "隨堂考中平均最低的是 {low_item}",
    ),
    (
        r"成績|分數|平均|intention|codinghelp",
        "各成績項目的平均與中位數",
        "gs[['item', 'students', 'mean', 'median', 'p25', 'p75']]",
        "各成績項目中，平均最低的是 {low_item}",
    ),
    (
        r"參與|作答人數|多少人|人數|變少|投入",
        "各單元的作答人數變化",
        "ps.assign(unit=ps.problem_id.str.extract(r'PY-(C\\d+)')[0])"
        ".groupby('unit', sort=False).students_attempted.max()",
        "作答人數到 {drop} 明顯變少（比前一個單元少 40% 以上）",
    ),
    (
        r".",  # 其他問題：看哪幾題最難
        "Accepted 比例最低（最多次沒通過）的題目",
        "ps.dropna(subset=['accepted_share']).sort_values('accepted_share')"
        ".head(3)[['problem_id', 'title', 'accepted_share', 'median_submissions_per_student', 'wrong_answer_share', 'runtime_error_share']]",
        "提交中 Accepted 比例最低的是 {top}，學生在這幾題需要最多次嘗試",
    ),
]


METRIC_NAME = {
    "accepted_share": "提交中通過的比例",
    "runtime_error_share": "提交中執行錯誤的比例",
    "time_limit_share": "提交中超時的比例",
}

# 問題目內容（「有沒有考切片」「哪一題在練習 while」）時只檢索題目，不附統計
CONTENT_RE = re.compile(r"有沒有.*題|哪(一|幾|些)?(個)?題.*(考|練習|用到|在教)|考什麼|在考|題目內容|題目是|類似的題")

COURSE_PROMPT = """你是課程分析助手，請用繁體中文回答，簡潔、條列。
你拿到的是一門大學 Python 程式設計課程的彙總資料：每道練習題的題目說明，以及每題、每個成績項目的彙總統計。
規則：
1. 數字只能使用「資料事實」中出現的數字，不可以自己計算或編造新的數字；人數已四捨五入，不要報精確人數。
2. 引用題目時，用題號標註，例如 [PY-C2-A2]；只引用和問題有關的題目。
3. 「提交中通過的比例」低，代表學生要試很多次才通過，不是提交次數少。
4. 說明原因時一律寫成「可能」「推測」。資料沒有的事，直接說不知道。"""


@dataclass
class CourseAnswer:
    facts: list[str] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)


NOTE = "課程資料只有彙總統計，人數已四捨五入到 5 的倍數，只適合比較相對高低。"


def _detail(result) -> str | None:
    """把查詢結果完整列出來給模型。只給一個最低值時，3B 會自己編出其他項目的數字。"""
    if isinstance(result, pd.DataFrame) and "item" in result:
        parts = []
        for r in result.itertuples():
            mean = "不公開（人數太少）" if pd.isna(r.mean) else f"{int(r.mean)} 分"
            parts.append(f"{r.item}：平均 {mean}、中位數 {r.median:g} 分")
        return "完整列表（只有這些項目）：" + "；".join(parts)
    if isinstance(result, pd.Series):
        return "各單元作答人數（四捨五入）：" + "、".join(f"{u} {'不公開' if pd.isna(v) else int(v)}" for u, v in result.items())
    return None


def _describe(claim: str, query: str, result) -> str:
    if isinstance(result, pd.DataFrame) and "problem_id" in result:
        metric = result.columns[2]  # 每個查詢的第 3 欄就是排序用的指標
        name = METRIC_NAME.get(metric, metric)
        top = "、".join(f"{r.problem_id}「{r.title}」（{name} {getattr(r, metric):.0%}）" for r in result.itertuples())
        return claim.format(top=top)
    if isinstance(result, pd.DataFrame) and "item" in result:
        low = result.dropna(subset=["mean"]).sort_values("mean").head(1)
        return claim.format(low_item=f"{low.item.iloc[0]}（平均 {int(low['mean'].iloc[0])} 分）") if len(low) else claim
    if isinstance(result, pd.Series):
        s = result.dropna()
        drops = [u for prev, u in zip(s.index, s.index[1:]) if s[u] <= s[prev] * 0.6]
        return claim.format(drop=drops[0]) if drops else "各單元作答人數沒有明顯變化"
    return claim


def answer(message: str, trace: list[TraceStep] | None = None) -> CourseAnswer:
    ans = CourseAnswer()
    content_only = bool(CONTENT_RE.search(message))
    if content_only and trace is not None:
        trace.append(TraceStep(step="course_query", detail="問題目內容，只檢索題目、不查統計"))
    for pattern, label, query, claim in [] if content_only else QUERIES:
        if re.search(pattern, message, re.I):
            frames = _frames()
            result = eval(query, {"pd": pd}, frames)  # noqa: S307
            ev = Evidence(query=query, result=analytics.to_jsonable(result))
            text = _describe(claim, query, result)
            ans.facts.append(f"{label}：{text}")
            if detail := _detail(result):
                ans.facts.append(detail)
            ans.recommendations.append(Recommendation(claim=text, evidence=[ev]))
            if trace is not None:
                trace.append(TraceStep(step="course_query", detail=query))
            if isinstance(result, pd.DataFrame) and "problem_id" in result:
                # 統計題：sources 直接用統計結果點名的題目，事實和引用的題目才會一致
                probs = load_problems()
                ans.sources = [Source(id=pid, text=probs[pid]) for pid in result.problem_id if pid in probs]
            elif query.startswith("gs"):
                # 成績項目和個別題目無關，不檢索題目，免得模型被無關的題目帶偏
                ans.facts.append(NOTE)
                return ans
            break
    ans.facts.append(NOTE)
    if ans.sources:
        if trace is not None:
            trace.append(TraceStep(step="retrieve", detail=f"引用統計結果中的題目 {', '.join(s.id for s in ans.sources)}"))
        return ans
    try:
        ans.sources = search_problems(message)
        if trace is not None:
            trace.append(TraceStep(step="retrieve", detail=f"課程題目檢索（向量＋BM25）命中 {', '.join(s.id for s in ans.sources)}"))
    except retrieval.EmbedError as e:
        if trace is not None:
            trace.append(TraceStep(step="retrieve", detail=f"課程題目檢索失敗：{e}"))
    return ans


NUM_RE = re.compile(r"(?<![A-Za-z0-9-])\d+(?:\.\d+)?")


def unsupported_numbers(text: str, facts: list[str], sources: list[Source]) -> list[str]:
    """找出模型回答裡、但資料事實與引用題目中都沒有出現的數字（輕量的事實查核）。

    題號（PY-C1-A1）與單元代號（C3）裡的數字不算；個位數也略過，因為多半是條列的序號。
    只比對字面，所以模型把 0.06 寫成 6% 也會被當成「不在資料中」，寧可多提醒。"""
    pool = " ".join(facts) + " " + " ".join(s.text for s in sources)
    known = set(NUM_RE.findall(pool))
    bad = []
    for n in NUM_RE.findall(re.sub(r"PY-[A-Z0-9-]+|C\d+", " ", text)):
        if n in known or n in bad or (len(n) == 1 and n != "0"):
            continue
        bad.append(n)
    return bad
