"""問題分流（router）：先判斷問題屬於哪一類，再決定走哪條路、用哪個地端模型。

類別與處理：
    scores        需要查成績資料          → 成績分析＋檢索 → LOCAL_MODEL（3B）生成
    docs          只需查校務文件          → 成績分析＋檢索 → LOCAL_MODEL（3B）生成
    cause         問成績變化的原因        → 成績分析＋檢索 → LOCAL_MODEL（3B）生成
    chitchat      打招呼、道謝、閒聊      → 不查資料，CHAT_MODEL（0.5B）回覆，比較快
    unanswerable  需要資料裡沒有的欄位    → 護欄拒答（refuse），不生成
    course        問 Python 課程資料      → 課程題目檢索＋課程統計（course.py）→ LOCAL_MODEL 生成
    out_of_scope  不是校務的問題（解題、出題、教材摘要、家長問自己的孩子）→ 拒答並說明本系統的範圍
    crisis        自傷、霸凌、暴力、性騷擾等危機訊息 → 拒答，但附上求助專線（data/referral.md）
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

LABELS = ("scores", "docs", "cause", "chitchat", "unanswerable", "course", "out_of_scope", "crisis")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").strip().rstrip("/")
ROUTER_MODE = os.environ.get("ROUTER_MODE", "rule").strip()
# 只有 ROUTER_MODE=llm／fields 才用到；實驗中 3B 最好（docs/router-experiments.md）
ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "qwen2.5:3b").strip()
CHAT_MODEL = os.environ.get("CHAT_MODEL", "qwen2.5:0.5b").strip()
# Python 課程資料不是助教提供的資料，預設關閉；ENABLE_COURSE=1 才會分出 course 類
COURSE_ENABLED = os.environ.get("ENABLE_COURSE", "0").strip() == "1"

# ---------------------------------------------------------------- 規則

# 資料裡沒有的欄位：教師評價、出缺席、家庭背景與族群、性別、課外行為
# （第二版：依保留集 holdout 與組員檢查補上「導師」「出勤」「族群」「年資」「上課態度」等說法；
#   評估改用修改前就寫好的第二份保留集 tests/router_eval_holdout2.jsonl）
NO_FIELD_RE = re.compile(
    r"(哪位|哪個|哪些)(老師|教師|導師)|(老師|教師|導師).{0,8}(教得|教學能力|能力|評價|好不好|最差|最好|受歡迎|歡迎|年資|資歷)"
    r"|缺席|出缺席|出席率|出勤|(幾點|準時|按時|每天)到校|到校(時間|狀況|情形|率)|遲到|曠課|請假(最多|次數|天數|紀錄)"
    r"|家庭|單親|低收|收入|社經|父母|家長.{0,4}(職業|學歷|教育程度)|教育程度|原住民|族群"
    r"|性別|男生|女生|男女"
    r"|補習|讀書.{0,4}(小時|時間)|睡眠|手機|上課.{0,4}(專心|態度|秩序)"
)
GREETING_RE = re.compile(r"^((你好|您好|哈囉|嗨|hi|hello|早安|午安|晚安|謝謝|感謝|辛苦了|好的|了解|收到|ok)[!！~～,，、。.\s]*)+", re.I)
QUESTION_RE = re.compile(r"什麼|哪|多少|幾|如何|怎麼|嗎|\?|？")
CHAT_RE = re.compile(r"你是誰|你是什麼|你(可以|能)(幫我)?做什麼|^我(叫|是)|我剛剛說", re.I)

# Python 課程資料（data/course/python/）。只有規則會分出這一類；llm／fields 的 prompt 沒有這一類
# 「強」詞只會出現在課程問題；「弱」詞（題目、作業、通過率…）校務問題也會用，
# 只有在問題完全沒有校務線索（年級、班級、段考、學期、科目…）時才算課程問題。
COURSE_STRONG_RE = re.compile(
    r"python|程式|練習題|隨堂考|runtime|time ?limit|accepted|PY-[A-Z0-9]|intention|codinghelp"
    r"|迴圈|while|for 迴圈|函式|function|字典|dict|串列|list|切片|slice",
    re.I,
)
COURSE_WEAK_RE = re.compile(r"題目|作業|提交|通過率|小考|超時")
SCHOOL_RE = re.compile(
    r"[七八九789]\s*年級|國[一二三]|(?<!\d)[789]\d{2}\s*班|班級|各班|段考|會考|學期|(?<!\d)1\d{3}(?!\d)"
    r"|國文|英文|英語|數學|自然|社會|學習扶助|補救|全校|校務|校長|導師|任課"
    r"|研究會|會議|代理|試辦|晨讀|實驗室|讀者劇場|家長會|教務處|學務處|總務處|輔導室|哪一?個?班"
)
SCORE_RE = re.compile(r"成績|分數|平均|表現|數據|下滑|下降|退步|進步|缺考|及格|關注|建議|改善|(?<!\d)1\d{3}(?!\d)")
CAUSE_RE = re.compile(r"原因|為什麼|關係|因為|有關|影響|所以")


# 危機訊息：安全優先，比其他判斷都早（依家長組公開題的一半寫成，另一半與 tests/scope_holdout.jsonl 用來評估）
CRISIS_RE = re.compile(
    r"不想活|想死|去死|死掉|自殺|輕生|自殘|割傷|割腕|活著.{0,2}累"
    r"|欺負|霸凌|排擠|勒索|嘲笑|被.{0,8}(打|揍)|罵(他|她)"
    r"|拿刀|威脅|瘀青|家暴|摸(他|她|我)|性騷擾|騷擾|曖昧|網友.{0,6}見面"
)
# 不是校務的問題：解方程式、要答案、出題、教材摘要、課程概念、家長問自己的孩子
OUT_OF_SCOPE_RE = re.compile(
    r"[xy]\s*[+\-−*/=)]|\d\s*\(?\s*[xy]\b|雞兔"
    r"|答案是多少|給我答案|直接(告訴|講|給)|只要數字|最後答案|解題過程"
    r"|出\s*[0-9一二三四五六七八九十兩]+\s*題|出題|幫我出|選擇題"
    r"|摘要|整理.{0,6}重點|重點整理|給學生複習"
    r"|^什麼是|是什麼意思|移項|等量公理|去分母|驗算|一元一次|應用問題.{0,4}步驟"
)
CHILD_RE = re.compile(r"我(的)?(孩子|小孩|女兒|兒子)|^(孩子|小孩|女兒|兒子|他(?!們)|她(?!們))")
# 4. 校長問霸凌的紀錄、統計、宣導（沒有提到特定的人）不是危機訊息；文件沒有時走一般流程回答不知道
RECORD_RE = re.compile(r"紀錄|統計|件數|案件|宣導|防治|政策|會議|校務|處理流程|通報流程")
PERSONAL_RE = re.compile(r"我|孩子|小孩|女兒|兒子|他|她|被")


def classify_rule(message: str) -> str:
    m = message.strip()
    if CRISIS_RE.search(m) and not (RECORD_RE.search(m) and not PERSONAL_RE.search(m)):
        return "crisis"
    if NO_FIELD_RE.search(m):
        return "unanswerable"
    # 開頭的問候語先拿掉；拿掉後還有實質內容（例如「你好，請問八年級數學平均多少？」）就照內容分類
    if CHAT_RE.search(m) and len(m) <= 15 and not (SCHOOL_RE.search(m.replace("校長", "")) and (SCORE_RE.search(m) or QUESTION_RE.search(m))):
        return "chitchat"
    rest = GREETING_RE.sub("", m)
    if GREETING_RE.match(m) and not (
        SCHOOL_RE.search(rest) or SCORE_RE.search(rest) or COURSE_STRONG_RE.search(rest) or COURSE_WEAK_RE.search(rest)
        or QUESTION_RE.search(rest)
    ):
        return "chitchat"
    # 含校務線索（年級、班級、段考、學期、科目、學習扶助…）的問題一律不當成範圍外，避免誤拒校長題
    if CHILD_RE.search(rest) or (OUT_OF_SCOPE_RE.search(rest) and not SCHOOL_RE.search(rest)):
        return "out_of_scope"
    if COURSE_ENABLED and (COURSE_STRONG_RE.search(rest) or (COURSE_WEAK_RE.search(rest) and not SCHOOL_RE.search(rest))):
        return "course"
    if SCORE_RE.search(rest):
        return "cause" if CAUSE_RE.search(rest) else "scores"
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
        return {"chitchat": CHAT_MODEL, "unanswerable": None, "out_of_scope": None, "crisis": None}.get(self.label, "LOCAL_MODEL")


def route(message: str, trace: list[TraceStep] | None = None, mode: str | None = None, model: str | None = None) -> Route:
    mode, model = mode or ROUTER_MODE, model or ROUTER_MODEL
    t = time.perf_counter()
    label, by, note = None, "rule", ""
    # 危機與範圍外一律先用規則判斷，不論分類方式：模型的類別清單沒有這兩類，交給模型會漏掉求助專線
    pre = classify_rule(message)
    if pre in ("crisis", "out_of_scope"):
        label, by = pre, "rule" if mode == "rule" else "rule（範圍檢查，先於模型）"
    elif mode in ("llm", "fields"):
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


CRISIS_REPLY = """謝謝您告訴我，這件事很重要。

這個系統是校長的校務分析助手，無法處理這類狀況，需要由專業人員協助：

- **1925 安心專線**（心理困擾、自殺防治，24 小時、免付費）；也可撥 **1995** 生命線、**1980** 張老師專線
- **1953 教育部反霸凌專線**（24 小時）
- **113 保護專線**（兒少保護、家暴、性侵害，24 小時）
- 有立即危險，請撥 **110**（報案）或 **119**（救護）

也請盡快聯繫孩子的導師或學校輔導室。"""


SAFETY_FOOTER = (
    "如果孩子有安全上的疑慮（例如自傷、霸凌、受到威脅或傷害），請撥 1925 安心專線、1953 反霸凌專線、113 保護專線；"
    "有立即危險請撥 110 或 119，並聯繫孩子的導師或學校輔導室。"
)


def out_of_scope_reply(message: str = "") -> str:
    """範圍外的回覆。家長談到自己的孩子時，一律附上求助專線：
    危機訊息的關鍵字一定有漏（例如「想消失」），寧可多給，也不要漏掉需要幫助的人。"""
    extra = "；也可查詢 Python 課程的彙總資料" if COURSE_ENABLED else ""
    text = (
        "這不在校長 AI 決策助手的範圍，所以我不回答。"
        "我只回答全校成績與校務文件相關的問題，例如各年級、班級、科目的成績變化，"
        f"或校務會議、教學研究會、學習扶助計畫記錄的事件{extra}。"
    )
    if CHILD_RE.search(message.strip()):
        text += "\n\n" + SAFETY_FOOTER
    return text


REFUSAL = (
    "這個問題需要的資訊不在本系統的資料裡，所以無法回答。"
    "成績資料只有學期、年級、班級、學號、科目與分數，沒有教師、出缺席、家庭背景等欄位；"
    "校務文件也沒有相關紀錄。可以改問各年級、班級、科目的成績變化，或校務文件中記錄的事件。"
)
