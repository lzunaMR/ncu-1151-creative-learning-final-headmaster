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

def unknown_reply() -> str:
    from .router import COURSE_ENABLED  # 課程資料關閉時，不要提到它

    extra = "；另有一門 Python 課程的彙總資料" if COURSE_ENABLED else ""
    return (
        "我在校務文件與成績資料中找不到可以回答這個問題的內容，所以**我不知道**，也不會用猜的回答。\n\n"
        "系統目前有的資料：全校三個學期的各科成績，以及本學期的校務會議、數學與英語教學研究會、學習扶助計畫等文件"
        f"{extra}。可以換個說法，或改問這些範圍內的問題。"
    )

JUDGE_PROMPT = """你是查核員。下面是系統找到的資料，請判斷：只根據這些資料，能不能回答使用者的問題？
資料沒有寫到的事，即使你知道答案，也算「不能」。
只輸出「能」或「不能」，不要解釋。

資料：
{context}

問題：{q}
判斷："""


# ---------------------------------------------------------------- 數字查核（校務流程）
# 「解讀與建議」是模型寫的，數字必須來自程式算出的資料事實、檢索到的文件或使用者的問題（prompt 規則 1）。
# 兩種錯誤都看過：①編出資料沒有的數字（八年級數學平均說成 59.92，資料是 59.58）；
# ②數字在資料裡，但說成別的對象的（903 班的 74.12 說成九年級的平均）。

NUM_RE = re.compile(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)?")
ID_RE = re.compile(r"\[?(?:S\d+-\d+-\d+|PY-[A-Z0-9-]+)\]?")
# 資料事實中「某班的值」：801 班 -4.44／805 班：64.64 → 52.24／705→805 班 -1.72
CLASS_VAL_RE = re.compile(r"(?<!\d)(\d{3}) 班[：:]?\s*([+-]?\d+(?:\.\d+)?)(?:\s*→\s*(\d+(?:\.\d+)?))?")
SENT_END_RE = re.compile(r"[。！？\n]")
CLAUSE_RE = re.compile(r"[，。；、！？\n（）()]")


def _numbers(text: str) -> list[str]:
    return NUM_RE.findall(ID_RE.sub(" ", text))


def unsupported_numbers(text: str, pool: list[str]) -> list[str]:
    """模型回答中、但資料事實／文件／問題都沒有出現的數字。用數值比對（12.4 與 12.40 視為相同）；
    個位整數略過，因為多半是條列序號或「三次段考」這類說法。"""
    known = {float(n) for t in pool for n in _numbers(t)}
    bad: list[str] = []
    for n in _numbers(text):
        if ("." not in n and len(n) == 1) or float(n) in known or n in bad:
            continue
        bad.append(n)
    return bad


def misattributed_numbers(text: str, facts: list[str]) -> list[tuple[str, str]]:
    """數字在資料事實中只是「某班的值」，但模型那句話（數字前面的部分與數字所在的短句）沒有提到那一班。
    回傳 (數字, 班級)。只檢查班級：這是實際看過的錯誤型態；年級、科目之間的張冠李戴不在這裡。"""
    owners: dict[float, set[str]] = {}
    for f in facts:
        for m in CLASS_VAL_RE.finditer(f):
            for v in (m.group(2), m.group(3)):
                if v:
                    owners.setdefault(abs(float(v)), set()).add(m.group(1))
    # 同一個數值也出現在「不是某班的值」的位置（例如年級平均），就不判斷，避免誤報
    others = {abs(float(n)) for f in facts for n in _numbers(CLASS_VAL_RE.sub(" ", f))}
    clean = ID_RE.sub(lambda m: " " * len(m.group(0)), text)
    wrong: list[tuple[str, str]] = []
    for m in NUM_RE.finditer(clean):
        v = abs(float(m.group(0)))
        if v not in owners or v in others or "." not in m.group(0):
            continue
        start = max((x.end() for x in SENT_END_RE.finditer(clean, 0, m.start())), default=0)
        cl_start = max((x.end() for x in CLAUSE_RE.finditer(clean, 0, m.start())), default=0)
        cl_end = next((x.start() for x in CLAUSE_RE.finditer(clean, m.end())), len(clean))
        context = clean[start:m.start()] + clean[cl_start:cl_end]
        if not any(c in context for c in owners[v]):
            pair = (m.group(0), "、".join(sorted(owners[v])))
            if pair not in wrong:
                wrong.append(pair)
    return wrong


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
