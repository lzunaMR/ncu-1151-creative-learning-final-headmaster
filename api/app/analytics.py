"""成績資料分析（校長 AI 決策助手）：用 pandas 查 scores.csv，產生 recommendations。

對外介面（pipeline.py 只用這兩個）：
    run_query(query: str) -> Evidence
        執行一段 pandas 運算式（變數 df 為整份 scores.csv，pd 為 pandas），
        回傳 Evidence(query=運算式字串, result=JSON 化的結果)。
    analyze(message: str, memory: dict | None = None, trace: list[TraceStep] | None = None) -> Analysis
        解析問題 → 決定要查什麼 → 執行查詢 → 回傳「資料事實」(facts) 與 recommendations。
        不需要查成績的問題回傳 Analysis(used=False)。memory 用來在多輪對話中沿用上一輪的年級、科目、班級。

evidence 可重跑（助教比對用）：
    import pandas as pd
    df = pd.read_csv("data/principal/scores.csv")
    eval(evidence.query)          # 與 evidence.result 相同（result 為 JSON 化後的版本）
    python scripts/check_evidence.py   # 自動逐筆重跑比對

- 空白分數代表缺考，pandas 讀入為 NaN；mean() 與 pivot_table 預設略過 NaN，因此缺考不計入平均。
- 不寫死結論：學期、年級、班級、科目全部由 df 的實際內容決定，換一份欄位相同的 csv 也能運作。
- 每個結論都由 run_query 的結果推出，facts 中的數字與 evidence.result 一致。
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from . import knowledge
from .schemas import Evidence, Recommendation, TraceStep

SCORES_CSV = Path(os.environ.get("PRINCIPAL_SCORES_CSV", "/data/principal/scores.csv").strip())
if not SCORES_CSV.exists():  # 不在 Docker 內時，改用 repo 內的資料
    SCORES_CSV = Path(__file__).resolve().parents[2] / "data" / "principal" / "scores.csv"


# ---------------------------------------------------------------- 資料與查詢


@lru_cache(maxsize=1)
def _load(path: str, mtime: float) -> pd.DataFrame:
    return pd.read_csv(path)


def load_scores() -> pd.DataFrame:
    """讀 scores.csv（檔案更新時自動重讀）。不做任何前處理，助教重跑 query 時才會得到相同結果。"""
    p = str(SCORES_CSV)
    return _load(p, os.path.getmtime(p))


def _py(v: Any) -> Any:
    """numpy 純量 → Python 純量；NaN → None。"""
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _key(k: Any) -> str:
    if isinstance(k, tuple):
        return "|".join(str(_py(x)) for x in k)
    return str(_py(k))


def to_jsonable(obj: Any) -> Any:
    """查詢結果轉成可放進 JSON 的格式：Series → {index: 值}；DataFrame → {列: {欄: 值}}。"""
    if isinstance(obj, pd.DataFrame):
        return {_key(i): {_key(c): _py(v) for c, v in row.items()} for i, row in obj.iterrows()}
    if isinstance(obj, pd.Series):
        return {_key(i): _py(v) for i, v in obj.items()}
    return _py(obj)


def _eval(query: str) -> Any:
    return eval(query, {"__builtins__": {}}, {"df": load_scores(), "pd": pd})  # noqa: S307 只執行本模組組出的運算式


def run_query(query: str) -> Evidence:
    """執行 pandas 運算式並回傳 Evidence。"""
    return Evidence(query=query, result=to_jsonable(_eval(query)))


def _query(query: str, trace: list[TraceStep] | None) -> tuple[Any, Evidence]:
    raw = _eval(query)
    if trace is not None:
        trace.append(TraceStep(step="analytics_query", detail=query))
    return raw, Evidence(query=query, result=to_jsonable(raw))


def _filter(conds: dict[str, Any]) -> str:
    """組出 df[(條件1)&(條件2)] 字串；值為 list 時用 isin。"""
    parts = []
    for col, v in conds.items():
        if v is None:
            continue
        ref = f"df[{col!r}]" if col == "class" else f"df.{col}"  # class 是 Python 保留字
        if isinstance(v, (list, tuple)):
            parts.append(f"({ref}.isin({[_py(x) for x in v]!r}))")
        else:
            parts.append(f"({ref}=={_py(v)!r})")
    return "df[" + "&".join(parts) + "]" if parts else "df"


# ---------------------------------------------------------------- 標籤

GRADE_CN = {7: "七年級", 8: "八年級", 9: "九年級"}


def grade_name(g: Any) -> str:
    g = _py(g)
    return GRADE_CN.get(g, f"{g} 年級")


def _label(dim: str, v: Any) -> str:
    if dim == "grade":
        return grade_name(v)
    if dim == "class":
        return f"{_py(v)} 班"
    return str(_py(v))


def _row_label(index: list[str], key: Any) -> str:
    keys = key if isinstance(key, tuple) else (key,)
    return "".join(_label(d, k) for d, k in zip(index, keys))


def _full_label(index: list[str], key: Any, filters: dict[str, Any]) -> str:
    """把分組 key 與篩選條件合成完整名稱，例如 index=['class']、key=805、filters 有數學 →「805 班數學」。"""
    keys = key if isinstance(key, tuple) else (key,)
    vals = {**{d: v for d, v in filters.items() if v is not None}, **dict(zip(index, keys))}
    s = _label("class", vals["class"]) if "class" in vals else (grade_name(vals["grade"]) if "grade" in vals else "")
    return s + (str(vals["subject"]) if "subject" in vals else "")


def _scope(filters: dict[str, Any]) -> str:
    s = ""
    if filters.get("class") is not None:
        s += _label("class", filters["class"])
    elif filters.get("grade") is not None:
        s += grade_name(filters["grade"])
    if filters.get("subject") is not None:
        s += str(filters["subject"])
    return s


DIM_NAME = {"grade": "年級", "class": "班級", "subject": "科目"}


# ---------------------------------------------------------------- 問題解析

SUBJECT_ALIASES = {"英語": "英文", "國語": "國文", "理化": "自然", "生物": "自然", "歷史": "社會", "地理": "社會", "公民": "社會"}
GRADE_RE = re.compile(r"([七八九789])\s*年級|國\s*([一二三])")
GRADE_CHAR = {"七": 7, "八": 8, "九": 9, "7": 7, "8": 8, "9": 9}
GUO_CHAR = {"一": 7, "二": 8, "三": 9}
CLASS_RE = re.compile(r"(?<!\d)(\d{3})(?!\d)")
SEM_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")
COUNT_RE = re.compile(r"([二兩三四五六2-6])\s*[個項點條]")
COUNT_CHAR = {"二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6}

TREND_RE = re.compile(r"下滑|退步|下降|變化|進步|比較|改變|差異|差距|成長|提升|影響|關係|起伏|趨勢")
CLASS_ASK_RE = re.compile(r"哪一?個?班|各班|班級|每一?班")
GRADE_ASK_RE = re.compile(r"哪一?個?年級|各年級")
SUBJECT_ASK_RE = re.compile(r"哪一?個?科|各科|哪些科")
LIST_RE = re.compile(r"列出|各科|各班|各年級|平均|一覽|多少分")
OVERVIEW_RE = re.compile(r"關注|注意|建議|改善|改進|問題|概況|整體|總覽|哪些地方|優先")
DATA_RE = re.compile(r"成績|分數|平均|表現|數據|下滑|退步|進步|關注|建議|改善")
FOLLOWUP_RE = re.compile(r"嚴重|最多|最少|最差|最好|那|呢|哪一?個?班")


@dataclass
class Intent:
    semesters: list[int]
    grade: Any = None
    klass: Any = None
    subject: str | None = None
    want_class: bool = False
    trend: bool = False
    listing: bool = False
    overview: bool = False
    n_wanted: int = 1


def _find_subjects(message: str, subjects: list[str]) -> list[str]:
    """問題提到的科目，依出現順序（別名換成正式名稱）。"""
    hits = [(message.find(s), s) for s in subjects if s in message]
    hits += [(message.find(a), t) for a, t in SUBJECT_ALIASES.items() if a in message and t in subjects]
    return list(dict.fromkeys(t for _, t in sorted(hits)))


def _find_subject(message: str, subjects: list[str]) -> str | None:
    found = _find_subjects(message, subjects)
    return found[0] if found else None


def parse(message: str, df: pd.DataFrame, memory: dict | None = None) -> Intent | None:
    """回傳 None 代表這題不需要查成績。"""
    memory = memory or {}
    sems = sorted(_py(s) for s in df.semester.unique())
    grades = {_py(g) for g in df.grade.unique()}
    classes = {str(_py(c)): _py(c) for c in df["class"].unique()}
    subjects = [str(s) for s in df.subject.unique()]

    sem_hits = sorted({int(m) for m in SEM_RE.findall(message) if int(m) in sems})
    grade = None
    for m in GRADE_RE.finditer(message):
        g = GRADE_CHAR.get(m.group(1) or "") or GUO_CHAR.get(m.group(2) or "")
        if g in grades:
            grade = g
            break
    klass = next((classes[c] for c in CLASS_RE.findall(message) if c in classes), None)
    subject = _find_subject(message, subjects)
    # 問題比較兩科以上（「英文和數學哪一科退步比較多？」）時不限定科目，改成各科一起比，
    # 否則只會算第一科，另一科的數字變成模型自己編（實際發生過）
    multi_subject = len(_find_subjects(message, subjects)) >= 2

    has_entity = bool(sem_hits or grade or klass is not None or subject)
    needs = has_entity or bool(DATA_RE.search(message))
    if knowledge.enabled("intent") and subject and not (sem_hits or grade or klass is not None):
        # 只有科目名稱（「英文的現在完成式怎麼用？」）不代表在問成績，還要有成績相關的詞
        needs = bool(DATA_RE.search(message) or TREND_RE.search(message) or LIST_RE.search(message)
                     or OVERVIEW_RE.search(message) or SUBJECT_ASK_RE.search(message))
    if not needs and memory and FOLLOWUP_RE.search(message):
        needs = True  # 追問，例如「那哪一班最嚴重？」
    if not needs:
        return None

    # 多輪對話：本輪完全沒提到學期、年級、班級、科目時（例如「那哪一班最嚴重？」），沿用上一輪；
    # 本輪有提到任何一項就視為新問題，不沿用，避免把上一題的條件帶錯
    if not has_entity and memory:
        if not GRADE_ASK_RE.search(message):
            grade = memory.get("grade")
        if not SUBJECT_ASK_RE.search(message):
            subject = memory.get("subject")
        sem_hits = list(memory.get("semesters") or [])

    if klass is not None and grade is None:
        grade = _py(df.loc[df["class"] == klass, "grade"].mode().iloc[0])

    n = 1
    m = COUNT_RE.search(message)
    if m:
        n = COUNT_CHAR.get(m.group(1)) or int(m.group(1))
    overview = bool(OVERVIEW_RE.search(message))
    if overview:
        n = max(n, 3)

    return Intent(
        semesters=sem_hits,
        grade=grade,
        klass=klass,
        subject=None if multi_subject else subject,
        want_class=bool(CLASS_ASK_RE.search(message)),
        trend=bool(TREND_RE.search(message)),
        listing=bool(LIST_RE.search(message)),
        overview=overview,
        n_wanted=n,
    )


def _semester_pair(intent: Intent, df: pd.DataFrame) -> tuple[int | None, int]:
    sems = sorted(_py(s) for s in df.semester.unique())
    if len(intent.semesters) >= 2:
        prev, cur = intent.semesters[-2], intent.semesters[-1]
    elif len(intent.semesters) == 1:
        cur = intent.semesters[0]
        earlier = [s for s in sems if s < cur]
        prev = earlier[-1] if earlier else None
    else:
        cur = sems[-1]
        prev = sems[-2] if len(sems) > 1 else None
    return prev, cur


def _same_school_year(a: int | None, b: int) -> bool:
    return a is not None and str(a)[:-1] == str(b)[:-1]


# ---------------------------------------------------------------- 分析結果


@dataclass
class Finding:
    fact: str
    claim: str
    evidence: list[Evidence]


@dataclass
class Analysis:
    used: bool = False
    facts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    memory: dict = field(default_factory=dict)


@dataclass
class Ctx:
    prev: int | None
    cur: int
    trace: list[TraceStep] | None
    notes: list[str]


def _fmt(x: Any) -> str:
    x = _py(x)
    return "—" if x is None else f"{x:.2f}"


def _change(ctx: Ctx, index: list[str], filters: dict[str, Any]) -> tuple[pd.DataFrame, Evidence]:
    """各組 prev、cur 兩學期平均與差值（change = cur - prev）。"""
    base = _filter({"semester": [ctx.prev, ctx.cur], **filters})
    idx = index[0] if len(index) == 1 else index
    q = (
        f"{base}.pivot_table(index={idx!r}, columns='semester', values='score', aggfunc='mean').round(2)"
        f".assign(change=lambda t: (t[{ctx.cur!r}] - t[{ctx.prev!r}]).round(2))"
    )
    raw, ev = _query(q, ctx.trace)
    return raw.dropna(subset=["change"]), ev


SMALL_CHANGE = 1.0  # 兩學期平均差距小於 1 分視為變化不大（不是結論，只是用語門檻）


def _trend_finding(ctx: Ctx, index: list[str], filters: dict[str, Any]) -> tuple[Finding, Any] | None:
    """依 index 分組比較兩學期，找出下滑最多的一組。回傳 (Finding, 該組的 key)。"""
    table, ev = _change(ctx, index, filters)
    if table.empty:
        return None
    scope = _scope(filters)
    head = f"{scope}：" if scope else ""
    dim = "×".join(DIM_NAME[d] for d in index)
    worst_key = table["change"].idxmin()
    w = table.loc[worst_key]
    name = _full_label(index, worst_key, filters)
    a, b, c = w[ctx.prev], w[ctx.cur], w["change"]
    move = "下滑" if c < 0 else "上升"

    if len(table) == 1:  # 只有一組（例如指定了年級與科目）
        fact = f"{name}：{ctx.prev} 學期平均 {_fmt(a)}，{ctx.cur} 學期 {_fmt(b)}，{move} {abs(c):.2f} 分。"
        claim = f"{name}在 {ctx.cur} 學期平均較 {ctx.prev} 學期{move} {abs(c):.2f} 分（{_fmt(a)} → {_fmt(b)}）"
        if abs(c) < SMALL_CHANGE:
            claim += f"，變化小於 {SMALL_CHANGE:g} 分，差異不大"
        elif c < 0:
            claim += "，建議追蹤"
        return Finding(fact, claim, [ev]), worst_key

    if len(table) <= 8:
        listing = "、".join(f"{_row_label(index, k)} {r['change']:+.2f}" for k, r in table.iterrows())
        detail = f"各{dim}變化：{listing}。"
    else:
        detail = f"共比較 {len(table)} 組{dim}。"
    if c < 0:
        fact = f"{head}{ctx.cur} 學期與 {ctx.prev} 學期相比，{detail}下滑最多的是 {_row_label(index, worst_key)}：{_fmt(a)} → {_fmt(b)}（{c:+.2f} 分）。"
        claim = f"{name}在 {ctx.cur} 學期平均較 {ctx.prev} 學期下滑 {abs(c):.2f} 分（{_fmt(a)} → {_fmt(b)}），是{scope}各{dim}中下滑最多的"
        claim += f"，但幅度小於 {SMALL_CHANGE:g} 分，差異不大" if abs(c) < SMALL_CHANGE else "，建議優先追蹤"
        if len(index) == 1:
            others = table.drop(index=worst_key)["change"].mean()
            fact = fact[:-1] + f"；其餘{DIM_NAME[index[0]]}平均變化 {others:+.2f} 分。"
    else:
        fact = f"{head}{ctx.cur} 學期與 {ctx.prev} 學期相比，{detail}沒有任何一組下滑；變化最小的是 {_row_label(index, worst_key)}（{c:+.2f} 分）。"
        claim = f"{scope}各{dim}在 {ctx.cur} 學期平均皆未低於 {ctx.prev} 學期，變化最小的是{name}（{c:+.2f} 分）"
    return Finding(fact, claim, [ev]), worst_key


def _means_finding(ctx: Ctx, filters: dict[str, Any], by: list[str]) -> Finding | None:
    base = _filter({"semester": ctx.cur, **filters})
    if len(by) == 2:
        q = f"{base}.pivot_table(index={by[0]!r}, columns={by[1]!r}, values='score', aggfunc='mean').round(2)"
    else:
        q = f"{base}.groupby({by[0]!r}).score.mean().round(2)"
    raw, ev = _query(q, ctx.trace)
    if raw.empty:
        return None
    scope = _scope(filters)
    if isinstance(raw, pd.DataFrame):
        rows = [
            f"{_label(by[0], i)}：" + "、".join(f"{c} {_fmt(v)}" for c, v in r.items()) for i, r in raw.iterrows()
        ]
        text = "；".join(rows)
    else:
        text = "、".join(f"{_label(by[0], k)} {_fmt(v)}" for k, v in raw.items())
    fact = f"{ctx.cur} 學期{scope}各{'×'.join(DIM_NAME[d] for d in by)}平均分數：{text}。"
    claim = f"{ctx.cur} 學期{scope}各{'×'.join(DIM_NAME[d] for d in by)}平均分數（缺考不計入）：{text}"
    evs = [ev]
    if not isinstance(raw, pd.DataFrame) and len(raw) > 1:
        lo = raw.idxmin()
        fact = fact[:-1] + f"；最低為{_label(by[0], lo)}（{_fmt(raw[lo])}）。"
    if by == ["class"] and filters.get("grade") is not None and filters.get("subject") is not None:
        # 各班平均之外，另外算整個年級的平均（全體學生平均，不是各班平均的平均），模型才不會自己算
        total, ev2 = _query(f"{base}.score.mean().round(2)", ctx.trace)
        if total == total:  # 不是 NaN
            fact = fact[:-1] + f"；{scope}整體平均 {_fmt(total)}。"
            claim += f"；整體平均 {_fmt(total)}"
            evs.append(ev2)
    return Finding(fact, claim, evs)


def _missing_finding(ctx: Ctx) -> Finding | None:
    q = f"{_filter({'semester': ctx.cur})}.assign(missing=lambda t: t.score.isna()).groupby('grade').missing.sum()"
    raw, ev = _query(q, ctx.trace)
    if raw.empty or raw.sum() == 0:
        return None
    g = raw.idxmax()
    fact = f"{ctx.cur} 學期缺考（分數空白）人次：" + "、".join(f"{grade_name(k)} {int(v)}" for k, v in raw.items()) + "。"
    claim = f"{ctx.cur} 學期{grade_name(g)}缺考人次最多（{int(raw[g])} 人次），建議確認缺考原因與補考安排"
    return Finding(fact, claim, [ev])


def _lowest_finding(ctx: Ctx) -> Finding | None:
    q = f"{_filter({'semester': ctx.cur})}.groupby(['grade', 'subject']).score.mean().round(2)"
    raw, ev = _query(q, ctx.trace)
    if raw.empty:
        return None
    k = raw.idxmin()
    label = _row_label(["grade", "subject"], k)
    fact = f"{ctx.cur} 學期各年級×科目中，平均最低的是{label}（{_fmt(raw[k])}）。"
    claim = f"{label}是 {ctx.cur} 學期各年級科目中平均最低的（{_fmt(raw[k])}），建議列入學習扶助的優先對象"
    return Finding(fact, claim, [ev])


def _best_finding(ctx: Ctx) -> Finding | None:
    table, ev = _change(ctx, ["grade", "subject"], {})
    if table.empty:
        return None
    k = table["change"].idxmax()
    r = table.loc[k]
    if r["change"] <= 0:
        return None
    label = _row_label(["grade", "subject"], k)
    fact = f"{ctx.cur} 學期與 {ctx.prev} 學期相比，進步最多的是{label}（{_fmt(r[ctx.prev])} → {_fmt(r[ctx.cur])}，{r['change']:+.2f} 分）。"
    claim = f"{label}在 {ctx.cur} 學期進步最多（{r['change']:+.2f} 分），可整理其做法供其他年級參考"
    return Finding(fact, claim, [ev])


# ---------------------------------------------------------------- 分析流程


def _cohort_class_finding(ctx: Ctx, grade: Any, subject: Any) -> Finding | None:
    """跨學年的班級比較：升年級不重新編班（703 → 803 是同一班學生），
    所以用學號鎖定「cur 學期的這個年級」那批學生，比較他們自己在兩個學期的成績，依班序（班級代號末兩碼）分組。"""
    if grade is None or _py(grade) - 1 < 7:
        return None  # 七年級的上一學年還在國小，資料裡沒有
    g = _py(grade)
    ids = f"df[(df.semester=={ctx.cur!r})&(df.grade=={g!r})].student_id"
    subj = f"&(df.subject=={_py(subject)!r})" if subject else ""
    q = (
        f"df[(df.semester.isin([{ctx.prev!r}, {ctx.cur!r}])){subj}&(df.student_id.isin({ids}))]"
        f".assign(cohort=lambda t: t['class'] % 100)"
        f".pivot_table(index='cohort', columns='semester', values='score', aggfunc='mean').round(2)"
        f".assign(change=lambda t: (t[{ctx.cur!r}] - t[{ctx.prev!r}]).round(2))"
    )
    raw, ev = _query(q, ctx.trace)
    table = raw.dropna(subset=["change"])
    if table.empty:
        return None
    label = lambda k: f"{(g - 1) * 100 + int(k)}→{g * 100 + int(k)} 班"  # noqa: E731
    k = table["change"].idxmin()
    w = table.loc[k]
    a, b, c = w[ctx.prev], w[ctx.cur], w["change"]
    listing = "、".join(f"{label(i)} {r['change']:+.2f}" for i, r in table.iterrows())
    what = f"{GRADE_CN.get(g, f'{g} 年級')}{_py(subject) or ''}"
    move = "下滑" if c < 0 else "上升"
    fact = (
        f"{what}（同一批學生跨學年追蹤）：{ctx.prev} → {ctx.cur}，各班變化：{listing}。"
        f"變化最不利的是 {label(k)}：{_fmt(a)} → {_fmt(b)}（{c:+.2f} 分）。"
    )
    claim = (
        f"{label(k)}的同一批學生，{what}平均從 {ctx.prev} 學期的 {_fmt(a)} {move}到 {ctx.cur} 學期的 {_fmt(b)}"
        f"（{c:+.2f} 分），是該年級各班中{'下滑最多' if c < 0 else '進步最少'}的"
    )
    ctx.notes.append(f"{ctx.prev} 與 {ctx.cur} 跨學年：班級比較以學號追蹤同一批學生（升年級不重新編班，例如 {label(k)}）。")
    return Finding(fact, claim, [ev])


def _drill_class(ctx: Ctx, grade: Any, subject: Any) -> list[Finding]:
    """在某年級某科內比較各班。跨學年時改為追蹤同一批學生（703 → 803 是同一班）。"""
    if not _same_school_year(ctx.prev, ctx.cur):
        f = _cohort_class_finding(ctx, grade, subject)
        if f is None:
            why = "七年級的上一學年還在國小，資料中沒有這批學生的成績" if grade is not None and _py(grade) == 7 else "找不到可追蹤的同一批學生"
            ctx.notes.append(f"{ctx.prev} 與 {ctx.cur} 跨學年，{why}，未做班級比較。")
            return []
        return [f]
    r = _trend_finding(ctx, ["class"], {"grade": grade, "subject": subject})
    return [r[0]] if r else []


def _overview(ctx: Ctx) -> list[Finding]:
    out: list[Finding] = []
    if ctx.prev is not None:
        r = _trend_finding(ctx, ["grade", "subject"], {})
        if r:
            out.append(r[0])
            g, s = r[1]
            if _same_school_year(ctx.prev, ctx.cur):
                rc = _trend_finding(ctx, ["class", "subject"], {})
                if rc:
                    out.append(rc[0])
            else:
                out += _drill_class(ctx, g, s)
    for f in (_lowest_finding, _best_finding if ctx.prev is not None else None, _missing_finding):
        if f:
            x = f(ctx)
            if x:
                out.append(x)
    return out


def _plan(intent: Intent, ctx: Ctx) -> list[Finding]:
    g, s, k = intent.grade, intent.subject, intent.klass
    has_prev = ctx.prev is not None
    trend = intent.trend or intent.want_class or not intent.listing

    if intent.overview and g is None and s is None and k is None:
        return _overview(ctx)

    if k is not None:
        if has_prev and trend and _same_school_year(ctx.prev, ctx.cur):
            out = []
            if s is not None:  # 同年級各班比較，看這班的位置
                r = _trend_finding(ctx, ["class"], {"grade": g, "subject": s})
                r2 = _trend_finding(ctx, ["grade"], {"grade": g, "subject": s})
                out += [x[0] for x in (r, r2) if x]
            r3 = _trend_finding(ctx, ["subject"], {"class": k})
            if r3:
                out.append(r3[0])
            return out
        f = _means_finding(ctx, {"class": k}, ["subject"])
        return [f] if f else []

    if intent.listing and not intent.trend and not intent.want_class:
        if g is not None and s is not None:
            by = ["class"]
        elif g is not None:
            by = ["subject"]
        elif s is not None:
            by = ["grade"]
        else:
            by = ["grade", "subject"]
        f = _means_finding(ctx, {"grade": g, "subject": s}, by)
        return [f] if f else []

    if not has_prev:
        ctx.notes.append(f"{ctx.cur} 之前沒有其他學期資料，只能列出平均。")
        f = _means_finding(ctx, {"grade": g, "subject": s}, ["subject"] if g is not None else ["grade"])
        return [f] if f else []

    if g is not None and s is not None:
        out = []
        r = _trend_finding(ctx, ["grade"], {"grade": g, "subject": s})
        if r:
            out.append(r[0])
        out += _drill_class(ctx, g, s)
        return out
    if s is not None:  # 某科各年級 → 下滑最多的年級內各班
        r = _trend_finding(ctx, ["grade"], {"subject": s})
        if not r:
            return []
        return [r[0]] + _drill_class(ctx, r[1], s)
    if g is not None:  # 某年級各科 → 下滑最多的科目內各班
        r = _trend_finding(ctx, ["subject"], {"grade": g})
        if not r:
            return []
        return [r[0]] + _drill_class(ctx, g, r[1])
    r = _trend_finding(ctx, ["grade", "subject"], {})
    if not r:
        return []
    gg, ss = r[1]
    return [r[0]] + _drill_class(ctx, gg, ss)


def analyze(message: str, memory: dict | None = None, trace: list[TraceStep] | None = None) -> Analysis:
    df = load_scores()
    intent = parse(message, df, memory)
    if intent is None:
        if trace is not None:
            trace.append(TraceStep(step="intent", detail="不需要查成績資料"))
        return Analysis(used=False, memory=dict(memory or {}))

    prev, cur = _semester_pair(intent, df)
    ctx = Ctx(prev=prev, cur=cur, trace=trace, notes=[])
    if trace is not None:
        parts = [f"學期 {prev}→{cur}" if prev else f"學期 {cur}"]
        if intent.grade is not None:
            parts.append(grade_name(intent.grade))
        if intent.klass is not None:
            parts.append(f"{intent.klass} 班")
        if intent.subject:
            parts.append(intent.subject)
        kind = "整體概況" if intent.overview else ("趨勢比較" if intent.trend or intent.want_class else "平均列表")
        trace.append(TraceStep(step="intent", detail=f"需要查成績資料（{kind}）：{'、'.join(parts)}"))

    if prev is not None and not _same_school_year(prev, cur) and intent.grade is not None:
        ctx.notes.append(f"{prev} 與 {cur} 跨學年：同一年級在兩個學期是不同屆的學生。")

    findings = _plan(intent, ctx)
    if len(findings) < intent.n_wanted:  # 要求的建議數不夠時，補上整體概況
        seen = {f.claim for f in findings}
        findings += [f for f in _overview(ctx) if f.claim not in seen]
    if intent.overview:  # 概況題只給要求的數量（至少 3 項）
        findings = findings[: intent.n_wanted]

    new_memory = {
        k: v
        for k, v in dict(memory or {}, grade=intent.grade, subject=intent.subject, semesters=intent.semesters).items()
        if v
    }
    return Analysis(
        used=True,
        facts=[f.fact for f in findings],
        notes=ctx.notes,
        recommendations=[Recommendation(claim=f.claim, evidence=f.evidence) for f in findings],
        memory=new_memory,
    )
