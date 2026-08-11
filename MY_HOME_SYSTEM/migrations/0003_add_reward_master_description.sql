-- reward_master に description カラムを追加する。
-- (旧: services/quest_service.py の sync_master_data() 内で実行時に行っていた処理を
--  正式なマイグレーションとして記録したもの)
ALTER TABLE reward_master ADD COLUMN description TEXT;
