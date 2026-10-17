"""展示介面。只負責呼叫 /chat 並顯示結果，邏輯都放在 api/。"""

import json
import os
import uuid

import httpx
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")

st.set_page_config(page_title="校長 AI 決策助手", layout="wide")
st.title("校長 AI 決策助手")

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.messages = []


def render_backend(resp: dict) -> None:
    """必備區塊二：後台工程驗收區。"""
    sources = resp.get("sources") or []
    with st.expander(f"Sources（{len(sources)}）"):
        if not sources:
            st.caption("無")
        for s in sources:
            st.markdown(f"**{s.get('id', '')}**")
            st.text(s.get("text", ""))

    g = resp.get("guardrail") or {}
    label = f"{'已觸發' if g.get('triggered') else '未觸發'} · {g.get('action', '—')}"
    with st.expander(f"護欄判定：{label}"):
        st.json(g)

    trace = resp.get("trace") or []
    with st.expander(f"Agent 思考步驟（{len(trace)}）"):
        if not trace:
            st.caption("無")
        for i, t in enumerate(trace, 1):
            st.markdown(f"{i}. **{t.get('step', '')}**：{t.get('detail', '')}")

    route = resp.get("route", "—")
    with st.expander(f"路由：{route}"):
        st.write({"local": "地端模型", "api": "雲端 API"}.get(route, "未回報"))

    # 校長組：每項建議的 evidence（查詢與結果）
    recs = resp.get("recommendations") or []
    with st.expander(f"建議與統計依據（{len(recs)}）"):
        if not recs:
            st.caption("無")
        for i, rec in enumerate(recs, 1):
            st.markdown(f"**建議 {i}：{rec.get('claim', '')}**")
            for ev in rec.get("evidence") or []:
                st.caption("查詢")
                st.code(ev.get("query", ""), language="python")
                st.caption("結果")
                result = ev.get("result")
                if isinstance(result, (dict, list)):
                    st.json(result)
                else:
                    st.code(str(result), language=None)

    # 教師組的額外欄位
    if resp.get("questions"):
        with st.expander("題目與 Critic 查核"):
            st.code(json.dumps(resp["questions"], ensure_ascii=False, indent=2), language="json")


# 必備區塊一：使用者對話／指令區
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m.get("resp"):
            render_backend(m["resp"])

if prompt := st.chat_input("輸入訊息…"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            r = httpx.post(
                f"{API_URL}/chat",
                json={"session_id": st.session_state.session_id, "message": prompt},
                timeout=180,
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            st.error(f"呼叫 API 失敗：{e}")
            st.stop()
        resp = r.json()
        st.markdown(resp["answer"])
        render_backend(resp)
    st.session_state.messages.append({"role": "assistant", "content": resp["answer"], "resp": resp})
