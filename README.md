# 1151 創意學習 期末專題

規格以[助教 repo](https://github.com/NCU-AIKslab/ncu-1151-creative-learning-final) 為準；規格更新時另行公告。問題請以 issue 提出。

## 本組說明（校長 AI 決策助手）

### 使用的模型

| 用途 | 模型 | 執行位置 | 設定 |
| --- | --- | --- | --- |
| 生成回覆 | `qwen2.5:3b` | 地端 Ollama（`route` 固定為 `local`） | `LOCAL_MODEL`；記憶體 8 GB 以下可改 `qwen2.5:1.5b` |
| 檢索嵌入 | `bge-m3` | 地端 Ollama | `EMBED_BACKEND=ollama` |
| 閒聊回覆 | `qwen2.5:0.5b` | 地端 Ollama | `CHAT_MODEL`；問題分流判定為閒聊時使用，不查資料，回覆約 1–2 秒 |

本組不使用雲端 API，不需要任何 key，也不需要建立 `.env`。

### Demo（助教用）

前提：已安裝並開啟 **Docker Desktop** 與 **Ollama**。在 repo 根目錄執行一行：

| 系統 | 指令 |
| --- | --- |
| Windows（PowerShell） | `powershell -ExecutionPolicy Bypass -File .\demo.ps1` |
| macOS／Linux | `./demo.sh` |

腳本會依序：檢查 Docker 與 Ollama → 讓 Ollama 接受容器連線（Windows 自動設定 `OLLAMA_HOST=0.0.0.0` 並重啟 Ollama）→ 下載缺少的模型（`qwen2.5:3b`、`qwen2.5:0.5b`、`bge-m3`）→ `docker compose up -d --build` → 等待就緒 → 開啟 http://localhost:8501 。

實測（Windows 11、Docker Desktop 29.9.2、Ollama 0.40.7，模型已下載）：乾淨資料夾第一次執行（含建置映像）79 秒完成，之後重跑約 10 秒。

- 切換到版本 B：`.\demo.ps1 -Version B`（macOS／Linux：`./demo.sh B`），介面「Agent 思考步驟」中的 retrieve 會顯示「版本B」
- 關閉：`.\demo.ps1 -Stop`（`./demo.sh stop`）

不用腳本也可以直接執行 `docker compose up --build`：所有設定都有預設值，API 啟動時也會自動檢查 Ollama、下載缺少的模型，連不到 Ollama 時會在 log 印出原因與修正方式（`/health` 也會回報 Ollama 狀態）。

#### 建議的 demo 問題

| 問題 | 看什麼 |
| --- | --- |
| 最近一學期（1142）哪個年級的數學平均表現下滑最多？ | 「資料顯示的事實」由 pandas 算出；「建議與統計依據」展開欄有可重跑的 `evidence.query` |
| 八年級數學下滑，哪一班的情況最嚴重？ | 接續上一題的追問；逐層下鑽到 805 班 |
| 805 班的數學成績變化，跟本學期的人事異動有沒有關係？ | Sources 引用 S1-2-1、S2-3-1（代理教師），原因標明為推測 |
| 這學期學校有哪些硬體設施的改變？ | 版本 A 與 B 的差異：A 的 Sources 有 S1-3-1，回答「七年級自然科新實驗室啟用」；切到 B 後 S1-3-1 排不進前 3（BM25 和原文「實驗室整修」沒有共用詞），模型回答「文件沒有提到硬體設施的改變」 |
| 給我三個校務改善建議，要附上數據 | 每項建議都附 evidence |
| 哪位老師教得最差？ | 問題分流判定為 unanswerable，護欄觸發 `refuse`，說明資料沒有教師欄位 |
| 你好，你可以幫我做什麼？ | 分流判定為閒聊，改用 `qwen2.5:0.5b` 回覆，不查資料（「Agent 思考步驟」可看到 router 與使用的模型） |

### 設定（選用，寫在 `.env` 或指令前的環境變數）

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `RETRIEVER_VERSION` | `A` | `A`：段落切塊＋bge-m3 向量檢索；`B`：A＋BM25（字元二元組）以 RRF 合併。改完執行 `docker compose up -d --force-recreate api`，或用 `demo.ps1 -Version B` |
| `RETRIEVER_K` | `3` | 每次檢索取幾個切塊 |
| `LOCAL_MODEL` | `qwen2.5:3b` | 生成用的地端模型（3B 以下） |
| `EMBED_BACKEND` | `ollama` | 檢索嵌入來源；`st` 僅供本機實驗（需 `api/requirements-dev.txt`） |
| `HISTORY_TURNS` | `6` | 送進模型的最近幾輪對話 |
| `OLLAMA_AUTO_PULL` | `1` | 啟動時自動下載缺少的模型；`0` 關閉 |
| `ROUTER_MODE` | `rule` | 問題分流：`rule` 關鍵字、`llm` 小模型直接分類、`fields` 小模型列出需要的資訊再對照、`off` 不分流（護欄固定放行）。比較見 `docs/router-experiments.md` |
| `ROUTER_MODEL` | `qwen2.5:3b` | `llm`／`fields` 分類用的模型 |
| `CHAT_MODEL` | `qwen2.5:0.5b` | 閒聊回覆用的模型 |

### 系統怎麼回答

1. 判斷問題是否需要查成績（`api/app/analytics.py`）。需要時用 pandas 查 `scores.csv`，比較兩個學期，逐層找出下滑最多的年級、科目、班級。每項結論放進 `recommendations`，附上實際執行的 pandas 查詢（`evidence.query`）與結果。
2. 檢索校務文件（`api/app/retrieval.py`），段落 id 放進 `sources`。
3. 生成（`api/app/pipeline.py`）：answer 分成「資料顯示的事實」（程式算出的數字）與「解讀與建議」（模型撰寫，原因一律標為推測）。

驗證 evidence 可重跑：先啟動 API，再執行 `python scripts/check_evidence.py tests/principal.jsonl`。

### 已知限制

- 護欄由問題分流（`api/app/router.py`）判定：問到資料沒有的欄位（教師、出缺席、家庭背景等）時回 `refuse`。預設的關鍵字規則在公開題與自訂題都沒有誤拒，但對沒見過的問法偵測率低（保留集 6 題只抓到 0 題），見 `docs/router-experiments.md`。
- 對話歷史存在記憶體，API 重啟後就會清空。
- Ollama 連不到時，回覆只列資料事實，並在 answer 與 trace 註明生成失敗。
- 成績比較只看同一年級標籤；跨學年（例如 1132 比 1141）時同一年級是不同屆學生，班級代號也會改變，回覆會加註並略過班級比較。

## 範圍

實作重點為後端：檢索管線（RAG）、代理決策（Agent）、地端模型路由、強制性護欄。介面使用 Streamlit 或 Gradio。

## 四大角色

各組負責一個角色（已抽籤）。

| 角色 | 定位 | 驗收點 |
| --- | --- | --- |
| 學生 AI 家教 | 引導而非解答 | 學生索答時，轉為鷹架式提問 |
| 教師 AI 助教 | 摘要出題 | 題目經 Fact Critic 查核，可回溯至教材原文 |
| 家長 AI 幫手 | 親師溝通 | 高風險情境攔截並轉介 |
| 校長 AI 決策助手 | 校務分析 | 每項建議可回溯統計依據 |

## 規格文件

| 文件 | 內容 |
| --- | --- |
| [spec/midterm.md](spec/midterm.md) | 期中繳交 |
| [spec/acceptance.md](spec/acceptance.md) | 期末驗收規則與測試集 |
| [spec/api-contract.md](spec/api-contract.md) | 系統輸入輸出格式 |
| [spec/ui-requirements.md](spec/ui-requirements.md) | 展示介面規範 |

## 時程

| 日期 | 事項 |
| --- | --- |
| 10/5 | 專題說明、期中說明 |
| 11/1（日）23:59 | 期中繳交 |
| 【待定】 | 期末驗收（公開題、隱藏題、demo） |

## 開始

1. 於本 repo 按 **Use this template** 建立各組 repo（設為公開）。
2. 於[繳交表](https://docs.google.com/spreadsheets/d/1tq_mzDNSG_6Qrgds9bGRuGxvYlRlBbsi/edit?gid=1749443549#gid=1749443549)填寫 repo 連結。
3. 組員各自以自己的帳號 commit，個人貢獻依 commit 紀錄評估。

## 共通技術規定

1. **Docker 封裝**：`docker compose up` 後 10 分鐘內可用。驗收機已預先安裝 Ollama 與各組指定的模型，不計入時間。
2. **地端模型路由**：程式中至少一條處理路徑使用 Ollama 地端模型，其餘可使用雲端 API。
   - Ollama 安裝於主機，不放入 Docker。
   - 模型限 3B 以下（預設 `qwen2.5:3b`），使用的模型需註明於各組 README。
   - 開發時可全部使用雲端 API，但需保留地端路徑；地端路徑於驗收機上檢查。
3. **API key**：置於 `.env`，不得 commit。repo 中僅放 `.env.example`。key 若曾 push 至 GitHub，需立即至服務商撤銷。
4. **資料**：使用 `data/` 與 `tests/` 中助教提供的資料。

## 安裝

### 1. Ollama

1. 至 https://ollama.com/download 下載安裝（Windows 請安裝 Windows 版，不要裝在 WSL 內）。
2. 下載模型：
   ```bash
   ollama pull qwen2.5:3b      # 約 2 GB
   ollama pull qwen2.5:1.5b    # 約 1 GB，記憶體 8 GB 以下適用；需同時修改 .env 的 LOCAL_MODEL
   ```
3. 測試：`ollama run qwen2.5:3b "你好"`

### 2. 啟動

```bash
cp .env.example .env
docker compose up --build
```

| 服務 | 網址 |
| --- | --- |
| Streamlit | http://localhost:8501 |
| API | http://localhost:8000（文件：http://localhost:8000/docs） |

不使用 Docker 時：

```bash
cd api && pip install -r requirements.txt && uvicorn app.main:app --reload
cd ui  && pip install -r requirements.txt && streamlit run app.py
```

繳交前需以 `docker compose up` 確認可執行。

### 疑難排解

- **Windows + WSL**：拉映像時 WSL 當機或出現 `exec format error`，於 PowerShell 執行 `wsl --update`、`wsl --shutdown`，重開 Docker Desktop，於 Troubleshoot → Clean / Purge data 清除後重新啟動。
- **Linux 主機**：Ollama 預設僅接受本機連線，容器無法連入。需以 `OLLAMA_HOST=0.0.0.0` 啟動 Ollama（systemd 安裝者於 `systemctl edit ollama` 加入 `Environment="OLLAMA_HOST=0.0.0.0"`）。
- **無法執行地端模型**：開發時可全部使用雲端 API，於 `pipeline.py` 加入雲端路徑並以 `.env` 切換；地端路徑需保留，`route` 需如實回傳。

## Repo 結構

```
.
├── spec/                   # 規格文件
├── data/                   # 助教提供的資料；容器內路徑為 /data
│   ├── textbook/           # 家教組、助教組教材
│   ├── parent/             # 家長組教材
│   ├── principal/          # 校長組成績資料與校務文件
│   └── referral.md         # 家長組轉介資源清單
├── tests/
│   ├── <role>.jsonl        # 各角色公開測試題
│   └── my_tests.jsonl      # 自訂測試案例
├── docs/
│   └── midterm-report.md   # 期中報告範本
├── api/
│   ├── requirements.txt
│   └── app/
│       ├── main.py         # 端點
│       ├── schemas.py      # 回傳格式，欄位名稱不得修改
│       └── pipeline.py     # 主要實作位置
├── ui/
│   └── app.py              # Streamlit，含兩個必備區塊
├── scripts/
│   └── run_tests.py        # 測試程式
├── docker-compose.yml
└── .env.example
```

## 實作

`api/app/pipeline.py` 中的 `TODO`：

1. `check_guardrail`：角色護欄
2. `retrieve`：檢索（校長組另含成績資料查詢）
3. 護欄觸發後的處理
4. 地端／雲端路由，並回傳 `route`

流程可自行改寫，回傳的 `ChatResponse` 格式需維持不變。新增套件需寫入 `api/requirements.txt`。

## 測試

```bash
python scripts/run_tests.py tests/my_tests.jsonl
python scripts/run_tests.py tests/<role>.jsonl
```

## 聯絡

助教：鍾昀潔（yuunjiee313@gmail.com）
