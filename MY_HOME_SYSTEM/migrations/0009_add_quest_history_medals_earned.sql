-- Q-L3 (Issue #409): クエスト完了時のメダルドロップ数を履歴に記録する。
-- 以前は quest_users.medal_count にしか加算されず、承認済み履歴をキャンセルしても
-- メダルだけが残っていた。_apply_quest_rewards が書き込み、_revert_and_delete_history が戻す。
-- 既存行は 0 (記録の無い古い履歴のキャンセルではメダルを戻さない)。
ALTER TABLE quest_history ADD COLUMN medals_earned INTEGER DEFAULT 0;
