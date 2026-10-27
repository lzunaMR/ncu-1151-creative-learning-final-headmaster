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

前提：已安裝並開啟 **Docker Desktop** 與 **Ollama**（模型不必先下載，腳本會補）。不需要 `.env`、不需要任何 API key。

**1. 下載專案**

```bash
git clone https://github.com/lzunaMR/ncu-1151-creative-learning-final-headmaster.git
cd ncu-1151-creative-learning-final-headmaster
```

**2. 啟動（一行）**

| 系統 | 指令 |
| --- | --- |
| Windows（PowerShell） | `powershell -ExecutionPolicy Bypass -File .\demo.ps1` |
| macOS／Linux | `./demo.sh` |

完成後瀏覽器會自動開啟 http://localhost:8501 。

**3.（選用）一行驗證整套系統**

```bash
python scripts/check_system.py
```

會送 34 項檢查（閒聊與多輪記憶、成績分析與追問、原因題、只查文件、護欄拒答、誠實回答、範圍外與危機訊息、學科知識題、數字查核、跨學年比較、Python 課程資料），逐項檢查回傳格式與內容。課程資料關閉時（預設）會略過 3 項課程題，最後印出「31/31 通過」；開啟時為「34/34 通過」。只用 Python 標準函式庫，不必安裝套件。公開題：`python scripts/run_tests.py tests/principal.jsonl`。

腳本會依序：檢查 Docker 與 Ollama → 讓 Ollama 接受容器連線（Windows 自動設定 `OLLAMA_HOST=0.0.0.0` 並重啟 Ollama）→ 下載缺少的模型（`qwen2.5:3b`、`qwen2.5:0.5b`、`bge-m3`）→ `docker compose up -d --build` → 等待就緒 → 開啟 http://localhost:8501 。

實測（Windows 11、Docker Desktop 29.9.2、Ollama 0.40.7，模型已下載）：連 Python 基底映像與所有快取都清除後，純建置約 54 秒，從執行 `demo.ps1` 到可用 2～3 分鐘（主要是下載約 200 MB 的套件，視網路而定）；之後重跑約 15 秒。

api 與 ui 共用根目錄 `Dockerfile` 建出的同一個映像（`headmaster-ai`），套件用 `uv` 平行下載、只安裝一次，映像總大小 762 MB（原本兩個映像共 1,266 MB），同樣條件下建置時間 72 秒 → 54 秒。

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
| 台北 101 有多高？ | 和所有資料都無關：直接回答「我不知道，也不會用猜的」，不交給模型（「Agent 思考步驟」的 honesty 會顯示相似度） |
| 圖書館開放到幾點？ | 主題相近但文件沒寫答案：模型回答「資料中沒有提到」，不會編一個時間 |
| 哪位老師教得最差？ | 問題分流判定為 unanswerable，護欄觸發 `refuse`，說明資料沒有教師欄位 |
| 3x + 5 = 20，x 是多少？ | 解題是家教組的範圍：分流判定為 `out_of_scope`，護欄 `refuse`，說明本系統只回答校務 |
| 孩子在學校被同學排擠 | 危機訊息：分流判定為 `crisis`，拒答、不給建議，附上 1953 反霸凌專線等求助管道（`data/referral.md`） |
| Python 課哪一題最多人卡關？（需 `-Course` 開啟） | 分流判定為 `course`，改查 Python 課程彙總資料（`data/course/python/`）；Sources 是被點名的題目，見 `docs/course-data.md` |
| 比較 1132 和 1141 學期，八年級數學哪一班退步最多？ | 跨學年時改用學號追蹤同一批學生（701→801 … 706→806），evidence 查詢含 `cohort` |
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
| `ENABLE_COURSE` | `0` | Python 課程彙總資料：`1` 開啟（不是助教提供的資料，預設關閉） |
| `KNOWLEDGE_GUARD` | `intent,rule,prompt` | 學科知識題（文法、公式、解題方法）的把關方法，`off` 全關；比較見 `docs/knowledge-guard.md` |
| `NO_ANSWER_SIM` | `0.40` | 沒有查到成績、且問題和所有校務文件的最高相似度低於此值時，直接回答「不知道」 |

### 額外資料：Python 課程彙總資料集（預設關閉）

`data/course/python/` 是一門 Python 程式設計課程（48 位學生）的練習題與**彙總統計**，由課程平台管理者授權公開，不含任何個人層級資料，去識別化做法見該資料夾的 README。

規格要求使用助教提供的資料，所以這份資料**預設關閉**，系統只用助教的校務資料回答。要展示時才開啟：`.\demo.ps1 -Course`（macOS／Linux：`./demo.sh A course`），或設定 `ENABLE_COURSE=1`。開啟後，問題提到 Python、練習題、隨堂考、迴圈等詞時才改查這份資料（`api/app/course.py`）；「題目、作業、通過率」這類校務問題也會用的詞，只有在問題沒有年級、班級、段考等校務線索時才算課程問題。

### 找不到資料時誠實回答「不知道」

最怕的是系統亂編答案。做法（實驗與數據見 `docs/honesty-experiments.md`）：

1. 沒有查到成績、且問題和所有校務文件都不像（相似度 < 0.40）→ 直接回答不知道，不生成。這個門檻在 28 題有答案的題目上不會誤擋任何一題。
2. prompt 規定資料沒有的事要說「資料中沒有提到，我不知道」，不可用相近的資訊代替特定的人名、數字、日期。
3. 生成後做數字查核與引用查核：回答中有資料沒有的數字、把某班的數字說成別的對象、或引用不符時附上警告。

介面打開時，系統會先自我介紹並列出可以點的範例問題；送出問題後會顯示「思考中…」；回答完成前，輸入框與範例按鈕會暫時停用（Streamlit 收到新輸入會重跑整頁，正在等的回答會被丟掉）。

### 系統怎麼回答

0. 問題分流（`api/app/router.py`）：危機訊息、非校務問題、需要資料沒有的欄位 → 護欄拒答；閒聊 → 小模型回覆；其餘往下走。
1. 判斷問題是否需要查成績（`api/app/analytics.py`）。需要時用 pandas 查 `scores.csv`，比較兩個學期，逐層找出下滑最多的年級、科目、班級。每項結論放進 `recommendations`，附上實際執行的 pandas 查詢（`evidence.query`）與結果。
2. 檢索校務文件（`api/app/retrieval.py`），段落 id 放進 `sources`。
3. 生成（`api/app/pipeline.py`）：answer 分成「資料顯示的事實」（程式算出的數字）與「解讀與建議」（模型撰寫，原因一律標為推測）。

驗證 evidence 可重跑：先啟動 API，再執行 `python scripts/check_evidence.py tests/principal.jsonl`。

### 已知限制

- 護欄由問題分流（`api/app/router.py`）判定：問到資料沒有的欄位（教師、出缺席、家庭背景等）時回 `refuse`。第二版關鍵字規則在修改前就寫好的保留集上 19/21、沒有誤拒；仍擋不住沒想到的說法（例如「新住民子女」「有特殊需求的學生」），見 `docs/router-experiments.md`。
- 校長組只回答校務：解題、出題、教材摘要（家教組、助教組的範圍）與家長問自己的孩子都會拒答；自傷、霸凌、暴力等危機訊息拒答並附上求助專線。寫規則前先寫好的保留集上，校長題 0 誤拒、非校務 5/6 攔下，但危機訊息只有 2/4 判斷正確（「想消失」「跟蹤」沒認出來）；家長談自己的孩子時，範圍外的回覆一律附上專線作為保護。學科知識題（文法、公式、解題方法）另外有把關（`docs/knowledge-guard.md`），開發集 0/16 次用自己的知識回答，保留集 10/12 題擋下；沒擋下的說法（例如「直角坐標的象限怎麼分？」）仍可能被模型用自己的知識回答，而且可能答錯。詳見 `docs/router-experiments.md` 第三版。
- 對話歷史存在記憶體，API 重啟後就會清空。
- Ollama 連不到時，回覆只列資料事實，並在 answer 與 trace 註明生成失敗。
- 跨學年（例如 1132 比 1141）的年級比較，同一年級是不同屆學生，回覆會加註；班級比較則用學號追蹤同一批學生（升年級不重新編班，703→803）。七年級的上一學年在國小，沒有資料可追蹤。

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
