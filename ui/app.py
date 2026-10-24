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
    st.session_state.pending = None

INTRO = """你好，我是**校長 AI 決策助手**，可以協助你：

- **分析全校成績**：比較 1132、1141、1142 三個學期各年級、班級、科目的平均，找出下滑或進步最多的地方，每項結論都附上可以重跑的查詢（evidence）。
- **查詢校務文件**：本學期的校務會議、數學與英語教學研究會、學習扶助計畫，回答時標註引用的段落。
- **推測可能原因**：把成績變化和文件記錄的事件對照，並清楚分開「資料顯示的事實」與「推測」。
- **Python 課程資料**：一門 Python 程式設計課的練習題與彙總統計。

資料裡沒有的事（例如評價老師、出缺席、家庭背景），或找不到相關內容時，我會直接說**不知道**，不會用猜的。

可以點下面的範例開始，或在最下方輸入問題："""

EXAMPLES = [
    "最近一學期（1142）哪個年級的數學平均表現下滑最多？",
    "805 班的數學成績變化，跟本學期的人事異動有沒有關係？",
    "學習扶助開辦哪些科目？",
    "給我三個校務改善建議，要附上數據",
    "Python 課哪一題最多人卡關？",
    "哪位老師教得最差？",
]


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
# 開場：系統先自我介紹並提供範例問題（只是介面上的說明，不經過 /chat、不寫進對話歷史）
with st.chat_message("assistant"):
    st.markdown(INTRO)
    cols = st.columns(2)
    for i, q in enumerate(EXAMPLES):
        if cols[i % 2].button(q, key=f"example-{i}", use_container_width=True):
            st.session_state.pending = q

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m.get("resp"):
            render_backend(m["resp"])

typed = st.chat_input("輸入訊息…")
prompt = typed or st.session_state.pending
st.session_state.pending = None

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        # 地端模型在 CPU 上可能要十幾秒，先讓使用者知道系統正在處理
        with st.spinner("思考中…（判斷問題類型 → 查成績與校務文件 → 地端模型撰寫回答，約需 5～30 秒）"):
            try:
                r = httpx.post(
                    f"{API_URL}/chat",
                    json={"session_id": st.session_state.session_id, "message": prompt},
                    timeout=300,
                )
                r.raise_for_status()
            except httpx.HTTPError as e:
                st.error(f"呼叫 API 失敗：{e}")
                st.stop()
        resp = r.json()
        st.markdown(resp["answer"])
        render_backend(resp)
    st.session_state.messages.append({"role": "assistant", "content": resp["answer"], "resp": resp})
