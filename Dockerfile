# api 與 ui 共用同一個映像：套件只安裝一次（pandas、numpy 不用各下載一份），也只匯出一個映像。
# 用 uv 安裝套件（平行下載），比 pip 快很多。兩個服務的啟動指令寫在 docker-compose.yml。
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /srv
RUN pip install --no-cache-dir uv
COPY api/requirements.txt api-requirements.txt
COPY ui/requirements.txt ui-requirements.txt
RUN uv pip install --system --no-cache -r api-requirements.txt -r ui-requirements.txt

COPY api/app ./app
COPY ui/app.py ./ui_app.py
