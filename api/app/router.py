"""問題分流（router）：先判斷問題屬於哪一類，再決定走哪條路、用哪個地端模型。

類別與處理：
    scores        需要查成績資料          → 成績分析＋檢索 → LOCAL_MODEL（3B）生成
    docs          只需查校務文件          → 成績分析＋檢索 → LOCAL_MODEL（3B）生成
    cause         問成績變化的原因        → 成績分析＋檢索 → LOCAL_MODEL（3B）生成
    chitchat      打招呼、道謝、閒聊      → 不查資料，CHAT_MODEL（0.5B）回覆，比較快
    unanswerable  需要資料裡沒有的欄位    → 護欄拒答（refuse），不生成
scores／docs／cause 走同一條路（成績分析會自己判斷要不要查成績），分錯之間不影響結果；
真正有代價的是錯成 unanswerable（誤拒）與錯成 chitchat（漏查資料）。

分類方式（環境變數 ROUTER_MODE）：
    rule    關鍵字規則，不呼叫模型；預設，實驗中唯一在兩個評測集都沒有誤拒的方式
    llm     請地端小模型直接選類別（ROUTER_MODEL）；輸出不合法時退回規則
    fields  請地端小模型列出「回答需要哪些資訊」，再對照系統有沒有這些資料（規格對 unanswerable 的定義）
    off     不分流（舊行為：護欄固定放行）
比較見 scripts/eval_router.py 與 docs/router-experiments.md。
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass

import httpx

from .schemas import TraceStep

LABELS = ("scores", "docs", "cause", "chitchat", "unanswerable")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").strip().rstrip("/")
ROUTER_MODE = os.environ.get("ROUTER_MODE", "rule").strip()
# 只有 ROUTER_MODE=llm／fields 才用到；實驗中 3B 最好（docs/router-experiments.md）
ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "qwen2.5:3b").strip()
CHAT_MODEL = os.environ.get("CHAT_MODEL", "qwen2.5:0.5b").strip()

# ---------------------------------------------------------------- 規則

# 資料裡沒有的欄位：教師評價、出缺席、家庭背景、性別、課外行為
NO_FIELD_RE = re.compile(
    r"(哪位|哪個|哪些)(老師|教師)|(老師|教師).{0,6}(教得|教學能力|能力|評價|好不好|最差|最好)"
    r"|缺席|出缺席|出席率|遲到|曠課|請假(最多|次數|天數)"
    r"|家庭|單親|低收|收入|社經|父母|家長.{0,4}(職業|學歷)"
    r"|性別|男生|女生|男女"
    r"|補習|讀書.{0,4}(小時|時間)|睡眠|手機"
)
CHAT_RE = re.compile(r"^(你好|您好|哈囉|嗨|hi|hello|早安|午安|晚安|謝謝|感謝|辛苦了)|你是誰|你(可以|能)(幫我)?做什麼|^我(叫|是)|我剛剛說", re.I)
SCORE_RE = re.compile(r"成績|分數|平均|表現|數據|下滑|退步|進步|缺考|關注|建議|改善|(?<!\d)1\d{3}(?!\d)")
CAUSE_RE = re.compile(r"原因|為什麼|關係|因為|有關|影響")


def classify_rule(message: str) -> str:
    m = message.strip()
    if NO_FIELD_RE.search(m):
        return "unanswerable"
    if CHAT_RE.search(m) and len(m) <= 15:
        return "chitchat"
    if SCORE_RE.search(m):
        return "cause" if CAUSE_RE.search(m) else "scores"
    return "docs"


# ---------------------------------------------------------------- 小模型

PROMPT = """你是國中「校長 AI 決策助手」的問題分類器。系統只有兩種資料：
1. 成績資料：學期、年級、班級、學號、科目、分數（分數空白代表缺考）
2. 校務文件：會議紀錄、人事異動、教學計畫、學習扶助、活動與日期

請把問題分成下列其中一類，只輸出類別代號，不要解釋：
scores：需要查成績資料（平均、比較、進步退步、哪班哪科、附數據的建議）
docs：只需要查校務文件
cause：問成績變化的原因，需要成績資料加校務文件
chitchat：打招呼、道謝、自我介紹、問你能做什麼
unanswerable：需要資料裡沒有的資訊（例如評價老師、出缺席、家庭背景、性別、補習）

例子：
問題：八年級國文平均幾分？ → scores
問題：晨讀是星期幾？ → docs
問題：九年級數學退步，可能跟什麼有關？ → cause
問題：嗨，你好 → chitchat
問題：哪個老師最認真？ → unanswerable

問題：{q} →"""


def classify_llm(message: str, model: str) -> str | None:
    resp = httpx.post(
        f"{OLLAMA_URL}/api/generate",
        json={"model": model, "prompt": PROMPT.format(q=message.strip()), "stream": False,
              "options": {"temperature": 0, "num_predict": 8}},
        timeout=120,
    )
    resp.raise_for_status()
    text = resp.json()["response"].strip().lower()
    return next((lb for lb in LABELS if lb in text), None)


FIELDS_PROMPT = """要回答下面這個問題，需要哪些資訊？從清單中選出所有需要的代號，只輸出代號（例如 A 或 AB），不要解釋。
A：學生成績（學期、年級、班級、科目、分數、缺考）
B：學校的會議紀錄與計畫（人事異動、教學進度、學習扶助、活動、日期）
C：老師的表現、評價、年資或個人資料
D：學生的出缺席、遲到、上課行為
E：學生的家庭背景、經濟、族群、性別
F：學生的課外生活（補習、讀書時間、睡眠、手機）
G：不需要任何資料（打招呼、道謝、問你是誰）

問題：{q}
代號："""
MISSING = set("CDEF")  # 系統沒有的資料


def classify_fields(message: str, model: str) -> str | None:
    """不直接問「是哪一類」，而是問「需要哪些資訊」，再對照系統有沒有這些資料（對應規格對 unanswerable 的定義）。"""
    resp = httpx.post(
        f"{OLLAMA_URL}/api/generate",
        json={"model": model, "prompt": FIELDS_PROMPT.format(q=message.strip()), "stream": False,
              "options": {"temperature": 0, "num_predict": 6}},
        timeout=120,
    )
    resp.raise_for_status()
    letters = set(re.findall(r"[A-G]", resp.json()["response"].upper()))
    if not letters:
        return None
    if letters & MISSING:
        return "unanswerable"
    if letters == {"G"}:
        return "chitchat"
    if {"A", "B"} <= letters:
        return "cause"
    return "scores" if "A" in letters else "docs"


# ---------------------------------------------------------------- 對外


@dataclass
class Route:
    label: str
    by: str  # rule 或模型名稱
    ms: float
    note: str = ""

    @property
    def gen_model(self) -> str | None:
        """這一類用哪個模型生成；None 代表不生成（拒答）。LOCAL_MODEL 由 pipeline 決定。"""
        return {"chitchat": CHAT_MODEL, "unanswerable": None}.get(self.label, "LOCAL_MODEL")


def route(message: str, trace: list[TraceStep] | None = None, mode: str | None = None, model: str | None = None) -> Route:
    mode, model = mode or ROUTER_MODE, model or ROUTER_MODEL
    t = time.perf_counter()
    label, by, note = None, "rule", ""
    if mode in ("llm", "fields"):
        try:
            fn = classify_llm if mode == "llm" else classify_fields
            label, by = fn(message, model), (model if mode == "llm" else f"{model} 欄位檢查")
            if label is None:
                note = "；模型輸出不是合法類別，改用規則"
        except Exception as e:  # noqa: BLE001  模型不可用時不擋回覆
            note = f"；{model} 失敗（{type(e).__name__}），改用規則"
    if label is None:
        label, by = classify_rule(message), "rule" if mode == "rule" else f"rule（{model} 退回）"
    r = Route(label, by, (time.perf_counter() - t) * 1000, note)
    if trace is not None:
        trace.append(TraceStep(step="router", detail=f"分類為 {label}（{by}，{r.ms:.0f} ms{note}）"))
    return r


REFUSAL = (
    "這個問題需要的資訊不在本系統的資料裡，所以無法回答。"
    "成績資料只有學期、年級、班級、學號、科目與分數，沒有教師、出缺席、家庭背景等欄位；"
    "校務文件也沒有相關紀錄。可以改問各年級、班級、科目的成績變化，或校務文件中記錄的事件。"
)
