# 期中繳交

## 期限與方式

- 期限：**11/1（日）23:59**
- 繳交 repo 即可，報告寫在 repo 內的 `docs/midterm-report.md`。

步驟：

1. 完成 `docs/midterm-report.md` 並 push。
2. 打上 `midterm` tag 並 push：
   ```bash
   git tag midterm
   git push origin midterm
   ```
3. 於[繳交表](https://docs.google.com/spreadsheets/d/1tq_mzDNSG_6Qrgds9bGRuGxvYlRlBbsi/edit?gid=1749443549#gid=1749443549)填寫 repo 連結。

以 `midterm` tag 指向的 commit 評分。

## 準備

1. 於助教 repo 按 Use this template 建立各組 repo（設為公開）。
2. 於繳交表填寫 repo 連結。
3. 依 README 完成安裝，確認 `docker compose up` 可執行、`/health` 回傳 200。

## 提供的資料

| 角色 | 教材（RAG 用） | 其他資料 |
| --- | --- | --- |
| 學生 AI 家教 | `data/textbook/` | — |
| 教師 AI 助教 | `data/textbook/` | — |
| 家長 AI 幫手 | `data/parent/` | `data/referral.md` |
| 校長 AI 決策助手 | `data/principal/docs/` | `data/principal/scores.csv` |

教材皆為未切塊的 Markdown 原文。每個 `###` 標題以段落 id 開頭，例如 `### 1-3-3 例題：解 3x + 5 = 20`。

1. 自行切塊，切法不限。
2. 每個切塊需記錄其涵蓋的段落 id（可為多個）。
3. `/chat` 回傳的 `sources[].id` 使用段落 id。

## 繳交內容

### A. 系統

| 項目 | 要求 |
| --- | --- |
| 可重現 | `docker compose up` 後 10 分鐘內可用（不含首次下載模型） |
| RAG 管線 | 切塊、嵌入與索引、檢索、生成；回覆需標註來源 |
| 兩個版本 | 基本版，以及至少一種改良版（例如更換切塊方式、混合檢索、重排序）；兩版需可切換執行 |
| `/chat` | 符合 [api-contract](api-contract.md) |
| 角色情境 | 至少 1 個角色情境可完整執行 |
| 介面 | 符合 [ui-requirements](ui-requirements.md) |

### B. 報告（`docs/midterm-report.md`）

依 `docs/midterm-report.md` 範本填寫：

1. 系統架構圖：標示切塊、索引、檢索、生成的位置，以及走地端模型的路徑。
2. 設計決定與理由：切塊方式、嵌入模型、K 值、檢索方法。
3. 評測集與命中紀錄：
   - 自訂至少 10 題，每題標示應命中的段落 id。
   - 每題記錄兩個版本的 Top-K 實際命中段落。
   - 兩個版本的命中率。
4. 失敗案例：至少 2 個，含原因假設與驗證方式。
5. 公開測試題結果：以 `scripts/run_tests.py` 執行 `tests/<role>.jsonl`，附上輸出與失敗原因說明。
6. AI 使用揭露表（含查核方式）。

## 不在期中範圍

- 護欄、Fact Critic、Agent。`guardrail` 欄位需存在，內容可固定回傳 `pass`。
- 公開測試題的通過率（需執行並附結果，但不計分）。
- 隱藏測試題。

## 評分

| 項目 | 配分 | 滿分條件 |
| --- | --- | --- |
| 系統可重現且符合規格 | 25 | `docker compose up` 可執行；`/chat` 符合格式；介面含兩個必備區塊；至少 1 個角色情境可完整執行 |
| 兩個版本與命中紀錄 | 30 | 至少 10 題；每題記錄兩個版本的 Top-K 命中段落；兩個版本的命中率；兩版可切換執行 |
| 失敗案例 | 20 | 至少 2 個；原因假設具體，驗證方式可行 |
| 設計決定與理由 | 15 | 切塊方式、嵌入模型、K 值、檢索方法皆附理由 |
| 公開測試題 | 5 | 附執行輸出與失敗原因說明 |
| AI 使用揭露 | 5 | 含查核方式 |

命中率高低不計分。
