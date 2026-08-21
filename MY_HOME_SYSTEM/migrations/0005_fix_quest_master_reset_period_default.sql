-- 0002 で追加した reset_period カラムのデフォルト値 'weekly_monday' は
-- is_within_reset_period() が扱えない未対応値であり、これが原因で全クエストの
-- 完了判定 (get_all_view_data の completedQuests 算出) が常に False になり、
-- クエストをクリアしても画面上クリア状態にならないバグを引き起こしていた。
-- quest_data.py の全クエストは実質「毎日系」のため、既存データを 'daily' に補正する。
UPDATE quest_master SET reset_period = 'daily' WHERE reset_period IS NULL OR reset_period = 'weekly_monday';
