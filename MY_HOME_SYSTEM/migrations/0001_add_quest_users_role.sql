-- quest_users に role カラムを追加し、既存の親/子ユーザーへ初期値を設定する。
-- (旧: services/quest_service.py の sync_master_data() 内で実行時に行っていた処理を
--  正式なマイグレーションとして記録したもの)
ALTER TABLE quest_users ADD COLUMN role TEXT;
UPDATE quest_users SET role = 'role_adult' WHERE user_id IN ('dad', 'mom') AND role IS NULL;
UPDATE quest_users SET role = 'role_child' WHERE user_id IN ('daughter', 'son', 'child') AND role IS NULL;
