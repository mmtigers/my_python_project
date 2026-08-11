-- quest_master に reset_period カラムを追加する。
-- (旧: services/quest_service.py の sync_master_data() 内で実行時に行っていた処理を
--  正式なマイグレーションとして記録したもの)
ALTER TABLE quest_master ADD COLUMN reset_period TEXT DEFAULT 'weekly_monday';
