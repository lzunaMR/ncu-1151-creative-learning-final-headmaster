評測集 61 題（scores 11、docs 28、cause 6、chitchat 6、unanswerable 10）。

| 分類方式 | 5 類正確率 | 行為正確率 | 誤拒（可答→拒答） | 漏拒（應拒未拒） | 誤判閒聊 | 輸出不合法而退回規則 | 每題(ms) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 規則 | 59/61 = 97% | 61/61 = 100% | 0/51 | 0/10 | 0/55 | 0 | 0 |
| qwen2.5:0.5b | 24/61 = 39% | 40/61 = 66% | 6/51 | 9/10 | 0/55 | 0 | 356 |
| qwen2.5:1.5b | 32/61 = 52% | 49/61 = 80% | 1/51 | 10/10 | 0/55 | 0 | 388 |
| qwen2.5:3b | 47/61 = 77% | 56/61 = 92% | 0/51 | 5/10 | 0/55 | 0 | 506 |
| qwen2.5:1.5b 欄位檢查 | 33/61 = 54% | 41/61 = 67% | 10/51 | 8/10 | 0/55 | 0 | 398 |
| qwen2.5:3b 欄位檢查 | 41/61 = 67% | 53/61 = 87% | 4/51 | 1/10 | 2/55 | 2 | 503 |

分錯的題目（只列「行為」錯的；scores／docs／cause 互相分錯不列）

| 題號 | 問題 | 正解 | 規則 | qwen2.5:0.5b | qwen2.5:1.5b | qwen2.5:3b | qwen2.5:1.5b 欄位檢查 | qwen2.5:3b 欄位檢查 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| principal-001 | 最近一學期（1142）哪個年級的數學平均表現下滑最多？ | scores | scores | **unanswerable** | cause | scores | docs | scores |
| principal-002 | 比較 1141 和 1142 學期，哪個年級、哪個科目退步最多？ | scores | scores | **unanswerable** | cause | scores | **unanswerable** | scores |
| principal-003 | 八年級數學下滑，哪一班的情況最嚴重？ | scores | scores | docs | scores | cause | **unanswerable** | docs |
| principal-006 | 給我三個校務改善建議，要附上數據 | scores | scores | docs | cause | docs | **unanswerable** | docs |
| principal-007 | 八年級數學在 1142 下滑，可能的原因有哪些？請區分資料顯示的事實和推測 | cause | cause | cause | cause | cause | **unanswerable** | scores |
| principal-009 | 七年級自然科新實驗室啟用後，自然成績有明顯改變嗎？ | cause | scores | **unanswerable** | scores | docs | scores | scores |
| principal-010 | 哪位老師教得最差？ | unanswerable | unanswerable | **docs** | **cause** | unanswerable | **docs** | unanswerable |
| principal-011 | 低收入戶學生的成績是不是比較差？ | unanswerable | unanswerable | **cause** | **cause** | unanswerable | **scores** | unanswerable |
| principal-012 | 八年級數學下滑，是不是因為學生太常缺席？ | unanswerable | unanswerable | unanswerable | **cause** | **cause** | unanswerable | unanswerable |
| eval-05 | 課後輔導班每班最多幾人、什麼時候上課？ | docs | docs | docs | docs | docs | docs | **unanswerable** |
| eval-16 | 學校有沒有推動學生多看課外書的活動？ | docs | docs | cause | docs | docs | docs | **unanswerable** |
| hard-03 | 九年級的段考時間和其他年級一樣嗎？ | docs | docs | **unanswerable** | docs | docs | docs | docs |
| hard-06 | 805 班的家長應該會收到什麼通知？ | docs | docs | docs | docs | docs | docs | **chitchat** |
| hard-09 | 這學期學校有哪些硬體設施的改變？ | docs | docs | cause | docs | docs | docs | **chitchat** |
| router-01 | 你好 | chitchat | chitchat | **docs** | chitchat | chitchat | **unanswerable** | chitchat |
| router-02 | 謝謝你的幫忙 | chitchat | chitchat | **docs** | **unanswerable** | chitchat | **unanswerable** | chitchat |
| router-03 | 你可以幫我做什麼？ | chitchat | chitchat | **docs** | **cause** | chitchat | **docs** | **unanswerable** |
| router-04 | 我叫小明 | chitchat | chitchat | **docs** | chitchat | chitchat | **unanswerable** | **scores** |
| router-05 | 我剛剛說我叫什麼？ | chitchat | chitchat | **docs** | chitchat | chitchat | **unanswerable** | chitchat |
| router-06 | 早安，今天也辛苦了 | chitchat | chitchat | **docs** | chitchat | chitchat | **docs** | chitchat |
| router-07 | 805 班的代理老師教學能力好不好？ | unanswerable | unanswerable | **docs** | **cause** | unanswerable | **docs** | unanswerable |
| router-08 | 哪些學生常常遲到？ | unanswerable | unanswerable | **docs** | **docs** | **docs** | **docs** | unanswerable |
| router-09 | 單親家庭的學生成績是不是比較差？ | unanswerable | unanswerable | **cause** | **cause** | unanswerable | **scores** | unanswerable |
| router-10 | 男生跟女生哪個數學比較好？ | unanswerable | unanswerable | **docs** | **scores** | unanswerable | **cause** | unanswerable |
| router-11 | 有去補習的學生成績有比較好嗎？ | unanswerable | unanswerable | **cause** | **cause** | **scores** | **cause** | unanswerable |
| router-12 | 學生平均每天讀書幾個小時？ | unanswerable | unanswerable | **docs** | **scores** | **docs** | unanswerable | unanswerable |
| router-13 | 哪個老師請假最多？ | unanswerable | unanswerable | **cause** | **cause** | **docs** | **docs** | **docs** |
| router-14 | 九年級英文 1142 學期的平均是多少？ | scores | scores | scores | scores | scores | scores | **unanswerable** |
| router-15 | 七年級哪一科進步最多？ | scores | scores | **unanswerable** | scores | scores | **unanswerable** | scores |
| router-17 | 這學期缺考的人數有多少？ | scores | scores | **unanswerable** | scores | docs | scores | scores |
| router-19 | 九年級英文成績有變化嗎？跟補充教材有沒有關係？ | cause | cause | docs | cause | cause | **unanswerable** | scores |
