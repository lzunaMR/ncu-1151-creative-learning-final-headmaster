"""展示介面。只負責呼叫 /chat 並顯示結果，邏輯都放在 api/。"""

import json
import os
import uuid

import httpx
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")

st.set_page_config(page_title="期末專題", layout="wide")
st.title("期末專題")  # TODO: 改成你們的角色名稱

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.messages = []


def render_backend(resp: dict) -> None:
    """必備區塊二：後台工程驗收區。"""
    with st.expander(f"Sources（{len(resp['sources'])}）"):
        if not resp["sources"]:
            st.caption("無")
        for s in resp["sources"]:
            st.markdown(f"**{s['id']}**")
            st.text(s["text"])

    g = resp["guardrail"]
    label = f"{'已觸發' if g['triggered'] else '未觸發'} · {g['action']}"
    with st.expander(f"護欄判定：{label}"):
        st.json(g)

    with st.expander(f"Agent 思考步驟（{len(resp['trace'])}）"):
        for i, t in enumerate(resp["trace"], 1):
            st.markdown(f"{i}. **{t['step']}**：{t['detail']}")

    with st.expander(f"路由：{resp['route']}"):
        st.write("地端模型" if resp["route"] == "local" else "雲端 API")

    # 教師組 / 校長組的額外欄位
    for key, title in (("questions", "題目與 Critic 查核"), ("recommendations", "建議與統計依據")):
        if resp.get(key):
            with st.expander(title):
                st.code(json.dumps(resp[key], ensure_ascii=False, indent=2), language="json")


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
