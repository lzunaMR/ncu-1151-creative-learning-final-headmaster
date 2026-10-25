"""學科知識題的把關：問文法、公式、定理、解題方法、讀書方法的問題，不是校務，不可以用模型自己的知識回答。

為什麼需要：問題含科目名稱（例如「英文的現在完成式怎麼用？」）時，成績分析會查到該科成績，
相似度門檻因此被跳過；模型看到有資料，就用自己的知識回答了文法（docs/router-experiments.md 第三版）。

四種方法（環境變數 KNOWLEDGE_GUARD，可用逗號組合；off 代表全關），比較見 docs/knowledge-guard.md：
    intent  成績分析只看到科目名稱、沒有「成績、平均、退步…」或年級、班級、學期時，不算成績問題（analytics.parse）
    prompt  在 prompt 加一條規則：學科知識題回答不在範圍
    llm     生成前請地端模型判斷「問學校的情況」還是「問學科知識」（KNOWLEDGE_MODEL）
    rule    關鍵字規則（只依開發集寫成）
"""

from __future__ import annotations

import os
import re

import httpx

from .schemas import TraceStep

DEFAULT = "intent,rule,prompt"  # 依 docs/knowledge-guard.md 的比較選定
METHODS = ("intent", "prompt", "llm", "rule")


def enabled(name: str) -> bool:
    """每次呼叫都重讀環境變數，評測腳本才能在同一個程序裡切換方法。"""
    raw = os.environ.get("KNOWLEDGE_GUARD", DEFAULT)
    return name in {m.strip() for m in raw.split(",")}


# ---------------------------------------------------------------- prompt

PROMPT_RULE = (
    "7. 本系統只回答這所學校的情況（成績、會議紀錄、計畫、活動、人事、日期）。問題如果是在問一般的學科知識或學習方法"
    "（文法、公式、定理、原理、解題步驟、讀書或寫作技巧），即使文件或成績資料提到同一個科目或單元，"
    "也只回答「這是學科知識的問題，不在校長決策助手的範圍，我不回答」，不要用自己的知識解釋。"
)

# ---------------------------------------------------------------- rule（只依開發集寫成）

KNOWLEDGE_RE = re.compile(
    r"怎麼(用|解|做|算|讀|背|證明)|原理|定理|定律|文法|公式"
)
# 曾經有「是什麼？」結尾：「每週一、三 16:10 開始的課是什麼？」（答案在 S4-2-1）因此被誤拒，已拿掉
# 校務線索（不含科目名稱：科目名稱本身不代表在問學校的情況）
SCHOOL_CONTEXT_RE = re.compile(
    r"[七八九789]\s*年級|國[一二三]|(?<!\d)[789]\d{2}\s*班|班級|各班|段考|學期|(?<!\d)1\d{3}(?!\d)"
    r"|學習扶助|補救|全校|校務|校長|導師|任課|老師|教師|成績|分數|平均|進度|會議|研究會|計畫|試辦|學生的"
)

# ---------------------------------------------------------------- llm

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").strip().rstrip("/")
KNOWLEDGE_MODEL = os.environ.get("KNOWLEDGE_MODEL", "qwen2.5:3b").strip()
LLM_PROMPT = """下面是校長問 AI 助手的一個問題。判斷它在問哪一種事，只輸出 A 或 B，不要解釋。
A：這所學校的情況，例如學生成績、班級、會議紀錄、教學進度、計畫、活動、人事、日期
B：一般的學科知識或學習方法，例如文法、公式、定理、原理、解題步驟、讀書或寫作技巧

問題：{q}
答案："""


def llm_says_knowledge(message: str) -> bool | None:
    resp = httpx.post(
        f"{OLLAMA_URL}/api/generate",
        json={"model": KNOWLEDGE_MODEL, "prompt": LLM_PROMPT.format(q=message.strip()), "stream": False,
              "options": {"temperature": 0, "num_predict": 3}},
        timeout=120,
    )
    resp.raise_for_status()
    text = resp.json()["response"].strip().upper()
    return True if text.startswith("B") else False if text.startswith("A") else None


def is_knowledge(message: str, trace: list[TraceStep]) -> bool:
    """rule／llm 方法：判斷是不是學科知識題。intent、prompt 不在這裡處理。"""
    m = message.strip()
    if enabled("rule") and KNOWLEDGE_RE.search(m) and not SCHOOL_CONTEXT_RE.search(m):
        trace.append(TraceStep(step="knowledge", detail="關鍵字規則：學科知識題"))
        return True
    if enabled("llm"):
        try:
            verdict = llm_says_knowledge(m)
        except Exception as e:  # noqa: BLE001  模型不可用時不擋回覆
            trace.append(TraceStep(step="knowledge", detail=f"{KNOWLEDGE_MODEL} 失敗（{type(e).__name__}），不判斷"))
            return False
        trace.append(TraceStep(step="knowledge", detail=f"{KNOWLEDGE_MODEL} 判斷：{'學科知識' if verdict else '學校的情況' if verdict is False else '輸出不合法'}"))
        return bool(verdict)
    return False


REFUSAL_MARK = "不在校長決策助手的範圍"


def cut_after_refusal(text: str) -> str:
    """模型照規則 7 說了「不在範圍」之後，有時還會繼續給建議（例如推薦不相關的課程標準）；只留拒答那句。
    這條是看過保留集（tests/knowledge_holdout.jsonl 的英文作文題）才加的，見 docs/knowledge-guard.md。"""
    i = text.find(REFUSAL_MARK)
    if i < 0:
        return text
    end = text.find("我不回答", i)
    return text[: end + len("我不回答")] + "。" if end >= 0 else text
