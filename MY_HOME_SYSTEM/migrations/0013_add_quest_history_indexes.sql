-- Issue #734 (REPOSITORY_AUDIT_2026-09-18 の AUDIT-004):
-- アプリで最も読み書きされる quest_history / user_inventory / reward_history に
-- ユーザー定義インデックスが1つも無かった。ベースライン(0000)が持つ索引は
-- power_usage / switchbot_meter_logs / device_records の3つだけで、いずれも
-- 「スケジューラが5〜10分間隔で書き込む時系列テーブル」向けのものだった。
--
-- その結果、全クライアントが10秒間隔(useGameData.ts の refetchInterval)で叩く
-- GET /api/quest/data が、1リクエストあたり quest_history を3回フルスキャンしていた。
-- #409 / #662 でアプリ側の N+1 と O(Q×H) は解消済みだが、削減できたのはクエリ「回数」
-- だけで、1クエリが読む「行数」は減っていない。行は単調増加する(保持期間削除が無い。
-- Issue #733)ため、1リクエストのコストがデータ量に線形比例して増え続ける。
--
-- 索引追加は書き込みを僅かに遅くし DB ファイルを増やすが、quest_history は
-- 書き込みが1日数十行・読み取りが1日数千回であり、トレードオフは明確に索引側が有利。

-- GET /api/quest/data の MAX(completed_at) 集約(GROUP BY user_id, quest_id)と、
-- 完了時の連続達成ボーナス・スパムチェック・前提クエスト判定
-- (WHERE user_id=? AND quest_id=? ... ORDER BY completed_at DESC LIMIT 1)を
-- 同じ索引でカバーする。
--
-- 末尾に status を含めているのは**被覆索引(covering index)にするため**。
-- 集約クエリの条件は status != 'rejected' だが、status が索引に無いと
-- 「SCAN ... USING INDEX」止まりで行ごとに本体テーブルを引きにいく。含めると
-- 「SCAN ... USING COVERING INDEX」になり、10秒ポーリングの最ホットパスが
-- 本体テーブルに一切触れなくなる(EXPLAIN QUERY PLAN で確認済み。
-- tests/test_db_indexes.py が回帰テストとして固定している)。
CREATE INDEX IF NOT EXISTS idx_quest_history_user_quest_completed
    ON quest_history (user_id, quest_id, completed_at DESC, status);

-- 承認待ち一覧(status='pending')と、期間で絞る承認済み一覧
-- (status='approved' AND completed_at >= ?)。
CREATE INDEX IF NOT EXISTS idx_quest_history_status_completed
    ON quest_history (status, completed_at DESC);

-- 所持アイテム一覧(WHERE user_id=? AND status='owned' ORDER BY purchased_at DESC)。
CREATE INDEX IF NOT EXISTS idx_user_inventory_user_status
    ON user_inventory (user_id, status);

-- YouTubeごほうび券のクールダウン判定
-- (WHERE user_id=? AND status='consumed' AND reward_id IN (...) ORDER BY used_at DESC)。
CREATE INDEX IF NOT EXISTS idx_user_inventory_user_reward_used
    ON user_inventory (user_id, reward_id, used_at DESC);

-- 購入スパムチェック(WHERE user_id=? AND reward_id=? ORDER BY redeemed_at DESC LIMIT 1)。
CREATE INDEX IF NOT EXISTS idx_reward_history_user_reward_redeemed
    ON reward_history (user_id, reward_id, redeemed_at DESC);
