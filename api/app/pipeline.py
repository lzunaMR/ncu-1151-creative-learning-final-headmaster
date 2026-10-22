"""主要流程（大項二）。

三個模組的介面約定（T12）：
    pipeline.handle(session_id: str, message: str) -> ChatResponse
        /chat 的唯一入口，回傳格式見 schemas.py 與 spec/api-contract.md（欄位名稱不可改）。
    retrieval.retrieve(query: str, k: int | None = None, version: str | None = None,
                       trace: list[TraceStep] | None = None) -> list[Source]
        校務文件檢索（大項一）。sources[].id 為段落 id；版本由 RETRIEVER_VERSION 切換。
    analytics.analyze(message: str, memory: dict | None = None,
                      trace: list[TraceStep] | None = None) -> analytics.Analysis
        成績資料分析。Analysis.facts 為程式算出的資料事實，Analysis.recommendations 每項都附 evidence。
    analytics.run_query(query: str) -> Evidence
        執行一段 pandas 運算式（df = scores.csv），evidence 可原樣重跑。

流程：
    護欄（期中固定 pass）→ 判斷是否需要查成績（analytics）→ 檢索校務文件（retrieval）
    → 組 prompt（資料事實 + [段落id] 標註的文件）→ 生成（Ollama 地端模型）→ 寫入對話歷史

回覆的分工：數字與統計結論由 analytics 用 pandas 算出、原樣放進 answer 的「資料顯示的事實」；
模型只負責「解讀與建議（含推測）」，不讓 3B 模型自己算數字，也讓事實和推測分開。

生成模型：只使用 Ollama 地端模型（LOCAL_MODEL，預設 qwen2.5:3b），本組不使用雲端 API，route 固定為 local。
"""

from __future__ import annotations

import os
from collections import defaultdict

import httpx

from . import analytics, retrieval, router
from .schemas import ChatResponse, Guardrail, Source, TraceStep


def _env(name: str, default: str = "") -> str:
    # .strip()：Windows 上編輯的 .env 可能帶有 \r
    return os.environ.get(name, default).strip()


# 不用 localhost：Windows 會先試 IPv6 ::1，每次連線多等約 2 秒
OLLAMA_URL = _env("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
LOCAL_MODEL = _env("LOCAL_MODEL", "qwen2.5:3b")
HISTORY_TURNS = int(_env("HISTORY_TURNS", "6") or 6)  # 送進模型的最近幾輪對話

# session_id → 對話歷史（只存使用者原話與最終回覆）。重啟就會消失
_history: dict[str, list[dict]] = defaultdict(list)
# session_id → 上一輪查成績用到的年級、科目、學期，讓「那哪一班最嚴重？」這類追問能接續
_memory: dict[str, dict] = defaultdict(dict)

SYSTEM_PROMPT = """你是國中校長的 AI 決策助手，請用繁體中文回答，簡潔、條列。
規則：
1. 數字只能使用「資料事實」中出現的數字，不可以自己計算或編造新的數字。
2. 引用校務文件時，在句尾用 [段落id] 標註，例如 [S1-2-1]；只引用和問題有關的段落，無關的不要提。
3. 成績資料只有學期、年級、班級、學號、科目、分數，沒有教師、出缺席、家庭背景等資訊。
4. 說明原因時一律寫成「可能」「推測」，因為文件提到某件事，不代表成績一定受它影響。
5. 資料與文件都沒有的事，直接說不知道。"""


# ---------------------------------------------------------------- 護欄（T16）


def check_guardrail(route: router.Route | None, trace: list[TraceStep]) -> Guardrail:
    """依分流結果決定護欄：問到資料沒有的欄位（unanswerable）就拒答。ROUTER_MODE=off 時固定放行。"""
    if route is not None and route.label == "unanswerable":
        trace.append(TraceStep(step="guardrail", detail="問題需要資料中沒有的欄位，拒答（refuse）"))
        return Guardrail(triggered=True, category="unanswerable", action="refuse")
    trace.append(TraceStep(step="guardrail", detail="放行（pass）"))
    return Guardrail(triggered=False, category=None, action="pass")


# ---------------------------------------------------------------- 資料取得


def analyze_scores(session_id: str, message: str, trace: list[TraceStep]) -> analytics.Analysis:
    try:
        result = analytics.analyze(message, _memory[session_id], trace)
    except Exception as e:  # noqa: BLE001  分析失敗不擋回覆，trace 照實寫
        trace.append(TraceStep(step="analytics", detail=f"成績分析失敗：{type(e).__name__}: {e}"))
        return analytics.Analysis(used=False)
    if result.used:
        _memory[session_id] = result.memory
        trace.append(TraceStep(step="analytics", detail=f"產生 {len(result.recommendations)} 項有 evidence 的結論"))
    return result


def retrieve(message: str, trace: list[TraceStep]) -> list[Source]:
    try:
        return retrieval.retrieve(message, trace=trace)
    except Exception as e:  # noqa: BLE001
        trace.append(TraceStep(step="retrieve", detail=f"檢索失敗：{type(e).__name__}: {e}"))
        return []


# ---------------------------------------------------------------- 生成（T14：只走地端）


def generate_local(messages: list[dict], model: str | None = None) -> str:
    resp = httpx.post(
        f"{OLLAMA_URL}/api/chat",
        json={"model": model or LOCAL_MODEL, "messages": messages, "stream": False, "options": {"temperature": 0.3}},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


class GenerationError(RuntimeError):
    pass


def generate(messages: list[dict], trace: list[TraceStep], model: str | None = None) -> str:
    """呼叫 Ollama 地端模型產生回覆；失敗時丟 GenerationError，trace 照實記錄。"""
    model = model or LOCAL_MODEL
    try:
        text = generate_local(messages, model)
    except Exception as e:  # noqa: BLE001
        msg = f"{type(e).__name__}: {e}"
        trace.append(TraceStep(step="generate", detail=f"地端 Ollama {model} 失敗：{msg}"))
        raise GenerationError(msg) from e
    trace.append(TraceStep(step="generate", detail=f"使用地端 Ollama {model}（route=local）"))
    return text.strip()


CHAT_PROMPT = "你是國中校長的 AI 決策助手，請用繁體中文簡短回覆。你能做的事：分析全校成績資料、查詢校務會議文件、提出附數據的校務建議。"


# ---------------------------------------------------------------- 組裝（T13）


def build_messages(message: str, history: list[dict], analysis: analytics.Analysis, sources: list[Source]) -> list[dict]:
    parts = [SYSTEM_PROMPT]
    if analysis.used:
        facts = "\n".join(f"- {f}" for f in analysis.facts + analysis.notes) or "- （查無資料）"
        parts.append(f"資料事實（程式用 pandas 從成績資料算出，已另外列給使用者看）：\n{facts}")
    docs = "\n\n".join(f"[{s.id}] {s.text}" for s in sources)
    parts.append(f"校務文件：\n{docs or '（無）'}")
    if analysis.used:
        parts.append(
            "回答方式：不要重抄上面的數字表格。直接寫出對這些事實的解讀、可能的原因（標明是推測，"
            "有文件依據時附 [段落id]），以及具體可行的建議。"
        )
    return [
        {"role": "system", "content": "\n\n".join(parts)},
        *history[-2 * HISTORY_TURNS :],
        {"role": "user", "content": message},
    ]


def compose_answer(analysis: analytics.Analysis, model_text: str) -> str:
    if not analysis.used:
        return model_text
    lines = ["【資料顯示的事實】（由成績資料計算，查詢式見 recommendations 的 evidence）"]
    lines += [f"- {f}" for f in analysis.facts] or ["- 查無符合條件的資料"]
    lines += [f"- 註：{n}" for n in analysis.notes]
    lines += ["", "【解讀與建議】（含推測，非資料直接證實）", model_text]
    return "\n".join(lines)


def handle(session_id: str, message: str) -> ChatResponse:
    trace: list[TraceStep] = []
    history = _history[session_id]

    route = None if router.ROUTER_MODE == "off" else router.route(message, trace)
    guardrail = check_guardrail(route, trace)

    if guardrail.action == "refuse":
        answer = router.REFUSAL
        history += [{"role": "user", "content": message}, {"role": "assistant", "content": answer}]
        return ChatResponse(answer=answer, route="local", guardrail=guardrail, sources=[], trace=trace, recommendations=[])

    if route is not None and route.label == "chitchat":
        # 閒聊不查資料，交給小模型回覆
        messages = [{"role": "system", "content": CHAT_PROMPT}, *history[-2 * HISTORY_TURNS :], {"role": "user", "content": message}]
        try:
            answer = generate(messages, trace, router.CHAT_MODEL)
        except GenerationError as e:
            answer = f"（地端模型目前無法連線：{e}）"
        history += [{"role": "user", "content": message}, {"role": "assistant", "content": answer}]
        return ChatResponse(answer=answer, route="local", guardrail=guardrail, sources=[], trace=trace, recommendations=[])

    analysis = analyze_scores(session_id, message, trace)
    sources = retrieve(message, trace)
    messages = build_messages(message, history, analysis, sources)

    try:
        model_text = generate(messages, trace)
    except GenerationError as e:
        model_text = f"（地端模型目前無法連線，未產生文字說明：{e}）"

    answer = compose_answer(analysis, model_text)
    history += [{"role": "user", "content": message}, {"role": "assistant", "content": answer}]

    return ChatResponse(
        answer=answer,
        route="local",  # 只有地端一條路徑
        guardrail=guardrail,
        sources=sources or [],
        trace=trace or [],
        recommendations=analysis.recommendations or [],
    )
