#!/usr/bin/env bash
# 校長 AI 決策助手：macOS／Linux 一鍵 demo
#
# 用法（在 repo 根目錄執行）：
#   ./demo.sh          # 版本 A（預設）
#   ./demo.sh B        # 版本 B（向量＋BM25）
#   ./demo.sh A course # 一併開啟 Python 課程資料（預設關閉，不是助教提供的資料）
#   ./demo.sh stop     # 關閉
#
# 會做的事：檢查 Docker 與 Ollama → 下載缺少的模型 → docker compose up --build → 等待就緒 → 開啟瀏覽器
set -euo pipefail
cd "$(dirname "$0")"
MODELS=("qwen2.5:3b" "qwen2.5:0.5b" "bge-m3")

step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
fail() { printf '\n\033[31m[錯誤] %s\033[0m\n' "$1"; exit 1; }

if [ "${1:-}" = "stop" ]; then docker compose down; exit 0; fi
VERSION="${1:-A}"
[[ "$VERSION" =~ ^[AB]$ ]] || fail "版本只能是 A 或 B"

step "檢查 Docker"
command -v docker >/dev/null || fail "找不到 docker，請先安裝 Docker"
docker info >/dev/null 2>&1 || fail "Docker 沒有在執行，請先啟動 Docker 後再執行一次"

step "檢查 Ollama"
command -v ollama >/dev/null || fail "找不到 Ollama，請先安裝：https://ollama.com/download"
curl -sf -m 3 http://127.0.0.1:11434/api/version >/dev/null || fail "Ollama 沒有在執行，請先啟動 Ollama（ollama serve）"
if [ "$(uname)" = "Linux" ] && ss -ltn 2>/dev/null | grep -q '127.0.0.1:11434'; then
  fail "Ollama 只接受本機連線，容器連不到。請執行：sudo systemctl edit ollama，加入
  [Service]
  Environment=\"OLLAMA_HOST=0.0.0.0\"
然後 sudo systemctl restart ollama，再執行一次本腳本"
fi
echo "    Ollama 正常"

step "檢查模型"
tags="$(curl -sf http://127.0.0.1:11434/api/tags)"
for m in "${MODELS[@]}"; do
  if echo "$tags" | grep -q "\"name\":\"$m\(:latest\)\?\""; then echo "    $m 已安裝"
  else echo "    下載 $m（只有第一次需要）"; ollama pull "$m"; fi
done

step "建置並啟動容器（檢索版本 $VERSION）"
t0=$(date +%s)
COURSE=0; [ "${2:-}" = "course" ] && COURSE=1
RETRIEVER_VERSION="$VERSION" ENABLE_COURSE="$COURSE" docker compose up -d --build --force-recreate

step "等待服務就緒"
for _ in $(seq 1 200); do
  if curl -sf -m 3 http://localhost:8000/health >/dev/null && curl -sf -m 3 http://localhost:8501 >/dev/null; then
    ready=1; break
  fi
  sleep 3
done
[ "${ready:-}" = 1 ] || { docker compose logs --tail 40 api; fail "等候逾時，以上是 api 的 log"; }
health="$(curl -s http://localhost:8000/health)"
echo "$health" | grep -q '"ready":true' || printf '\n\033[33m[注意] API 回報：%s\n       請看 docker compose logs api 的 [ollama] 訊息\033[0m\n' "$health"

printf '\n\033[32m完成（%s 秒）。\033[0m\n' "$(( $(date +%s) - t0 ))"
echo "  介面：http://localhost:8501"
echo "  API ：http://localhost:8000/docs"
echo "  切換版本：./demo.sh B　　關閉：./demo.sh stop"
(command -v open >/dev/null && open http://localhost:8501) || (command -v xdg-open >/dev/null && xdg-open http://localhost:8501) || true
