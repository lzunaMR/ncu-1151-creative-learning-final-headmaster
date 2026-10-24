"""誠實回答「不知道」：資料裡找不到答案時，不交給模型自由發揮。

兩層把關（只在沒有查到成績資料時使用；有成績事實時，數字由程式算出，不會是編的）：
    1. 相似度門檻：問題和所有校務文件段落的最高餘弦相似度低於 SIM_THRESHOLD，代表根本不是文件談的事
       （天氣、電影、畢業旅行）。門檻依 tests/no_answer_eval.jsonl 與兩份檢索評測集實測決定，
       定在「所有有答案的題目」最低分之下，不會擋到有答案的題目。
    2. 可回答性檢查：主題相近、但文件沒寫答案的問題（圖書館開放到幾點、段考範圍），相似度和有答案的題目
       重疊，分不開；改請模型只回答「能／不能」：只根據檢索到的段落，能不能回答這個問題。
評測見 scripts/eval_honesty.py 與 docs/honesty-experiments.md。
"""

from __future__ import annotations

import os
import re

import httpx

from . import retrieval
from .schemas import Source

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").strip().rstrip("/")
SIM_THRESHOLD = float(os.environ.get("NO_ANSWER_SIM", "0.40"))
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "qwen2.5:3b").strip()

UNKNOWN = (
    "我在校務文件與成績資料中找不到可以回答這個問題的內容，所以**我不知道**，也不會用猜的回答。\n\n"
    "系統目前有的資料：全校三個學期的各科成績，以及本學期的校務會議、數學與英語教學研究會、學習扶助計畫等文件；"
    "另有一門 Python 課程的彙總資料。可以換個說法，或改問這些範圍內的問題。"
)

JUDGE_PROMPT = """你是查核員。下面是系統找到的資料，請判斷：只根據這些資料，能不能回答使用者的問題？
資料沒有寫到的事，即使你知道答案，也算「不能」。
只輸出「能」或「不能」，不要解釋。

資料：
{context}

問題：{q}
判斷："""


def top_similarity(query: str) -> float:
    """問題與所有校務文件段落（版本 A 的段落切塊）的最高餘弦相似度。"""
    idx = retrieval.get_index(retrieval.PRESETS["A"])
    q = retrieval.embed([query], "bge-m3")[0]
    return max(retrieval._dot(q, v) for v in idx.vectors)


def judge(query: str, sources: list[Source], facts: list[str] | None = None, model: str | None = None) -> bool | None:
    """模型判斷資料能不能回答問題；能 → True，不能 → False，輸出不合法或模型失敗 → None（當成能，不擋）。"""
    context = "\n\n".join([*(facts or []), *(f"[{s.id}] {s.text}" for s in sources)]) or "（無）"
    try:
        resp = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model or JUDGE_MODEL, "prompt": JUDGE_PROMPT.format(context=context, q=query.strip()),
                  "stream": False, "options": {"temperature": 0, "num_predict": 4}},
            timeout=120,
        )
        resp.raise_for_status()
        text = resp.json()["response"].strip()
    except Exception:  # noqa: BLE001
        return None
    if re.search(r"不能|无法|無法|不可以|否", text):
        return False
    if "能" in text or "可以" in text:
        return True
    return None
