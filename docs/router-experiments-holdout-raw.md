評測集 17 題（scores 3、docs 3、cause 2、chitchat 3、unanswerable 6）。

| 分類方式 | 5 類正確率 | 行為正確率 | 誤拒（可答→拒答） | 漏拒（應拒未拒） | 誤判閒聊 | 輸出不合法而退回規則 | 每題(ms) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 規則 | 7/17 = 41% | 8/17 = 47% | 0/11 | 6/6 | 1/14 | 0 | 0 |
| qwen2.5:0.5b | 7/17 = 41% | 8/17 = 47% | 0/11 | 6/6 | 0/14 | 0 | 338 |
| qwen2.5:1.5b | 11/17 = 65% | 11/17 = 65% | 0/11 | 5/6 | 0/14 | 0 | 390 |
| qwen2.5:3b | 11/17 = 65% | 12/17 = 71% | 1/11 | 4/6 | 0/14 | 0 | 514 |
| qwen2.5:1.5b 欄位檢查 | 6/17 = 35% | 9/17 = 53% | 1/11 | 5/6 | 0/14 | 0 | 382 |
| qwen2.5:3b 欄位檢查 | 13/17 = 76% | 15/17 = 88% | 0/11 | 2/6 | 0/14 | 3 | 531 |

分錯的題目（只列「行為」錯的；scores／docs／cause 互相分錯不列）

| 題號 | 問題 | 正解 | 規則 | qwen2.5:0.5b | qwen2.5:1.5b | qwen2.5:3b | qwen2.5:1.5b 欄位檢查 | qwen2.5:3b 欄位檢查 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| holdout-01 | 哪位導師最受學生歡迎？ | unanswerable | **docs** | **docs** | unanswerable | unanswerable | **docs** | unanswerable |
| holdout-02 | 學生的出勤狀況怎麼樣？ | unanswerable | **docs** | **docs** | **docs** | **docs** | **scores** | unanswerable |
| holdout-03 | 家長的教育程度會影響孩子的成績嗎？ | unanswerable | **cause** | **cause** | **cause** | **docs** | **scores** | **cause** |
| holdout-04 | 原住民學生的成績表現如何？ | unanswerable | **scores** | **cause** | **scores** | unanswerable | **scores** | unanswerable |
| holdout-05 | 哪一班的學生上課最不專心？ | unanswerable | **docs** | **docs** | **scores** | **docs** | unanswerable | unanswerable |
| holdout-06 | 數學老師的年資跟學生成績有關嗎？ | unanswerable | **cause** | **cause** | **cause** | **docs** | **docs** | **scores** |
| holdout-07 | 嗨～ | chitchat | chitchat | **docs** | chitchat | chitchat | **unanswerable** | chitchat |
| holdout-08 | 你是什麼系統？ | chitchat | **docs** | **docs** | **docs** | **unanswerable** | **docs** | chitchat |
| holdout-09 | 好的，了解，謝謝！ | chitchat | **docs** | **docs** | chitchat | chitchat | **docs** | chitchat |
| holdout-10 | 你好，請問八年級數學平均多少？ | scores | **chitchat** | scores | scores | scores | scores | scores |
