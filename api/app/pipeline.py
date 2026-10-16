"""主要流程。這是空殼：每個 TODO 都要換成你們自己的實作。

目前的流程：
  護欄檢查 → 檢索 → 產生回覆（Ollama 地端模型）
可以自由改成 Agent、加上路由、加上 Fact Critic 等。
唯一的要求是最後回傳的 ChatResponse 符合 spec/api-contract.md。
"""

import os
from collections import defaultdict

import httpx

from . import retrieval
from .schemas import ChatResponse, Guardrail, Source, TraceStep

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
LOCAL_MODEL = os.environ.get("LOCAL_MODEL", "qwen2.5:3b")

# session_id → 對話歷史。重啟就會消失，有需要請自行換成持久化儲存
_history: dict[str, list[dict]] = defaultdict(list)


def check_guardrail(message: str, trace: list[TraceStep]) -> Guardrail:
    # TODO: 依角色實作護欄（家教：索答偵測；家長：高風險分類……）
    trace.append(TraceStep(step="guardrail", detail="尚未實作，一律放行"))
    return Guardrail()


def retrieve(message: str, trace: list[TraceStep]) -> list[Source]:
    # 校務文件檢索見 retrieval.py（版本由 RETRIEVER_VERSION 切換）；成績資料查詢待 analytics.py
    return retrieval.retrieve(message, trace=trace)


def generate_local(messages: list[dict]) -> str:
    resp = httpx.post(
        f"{OLLAMA_URL}/api/chat",
        json={"model": LOCAL_MODEL, "messages": messages, "stream": False},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def handle(session_id: str, message: str) -> ChatResponse:
    trace: list[TraceStep] = []
    history = _history[session_id]

    guardrail = check_guardrail(message, trace)
    # TODO: 護欄觸發時改走對應的處理（鷹架引導、轉介、拒絕……）

    sources = retrieve(message, trace)
    context = "\n\n".join(f"[{s.id}] {s.text}" for s in sources)

    messages = [
        {"role": "system", "content": f"你是一個助理，請用繁體中文回答。參考資料：\n{context or '（無）'}"},
        *history,
        {"role": "user", "content": message},
    ]
    # TODO: 依需求路由到地端或雲端模型，並如實回報 route
    answer = generate_local(messages)
    trace.append(TraceStep(step="generate", detail=f"使用地端模型 {LOCAL_MODEL}"))

    history += [{"role": "user", "content": message}, {"role": "assistant", "content": answer}]

    return ChatResponse(
        answer=answer,
        route="local",
        guardrail=guardrail,
        sources=sources,
        trace=trace,
    )
