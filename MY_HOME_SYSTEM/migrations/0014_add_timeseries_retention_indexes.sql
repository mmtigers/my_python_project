-- Issue #733 (REPOSITORY_AUDIT_2026-09-18 の AUDIT-003):
-- SQLite の行の保持期間削除(services/db_retention_service.py)を入れるにあたり、
-- その DELETE が使う索引を用意する。
--
-- ベースライン(0000)が持つ時系列テーブルの索引は
--   idx_power_usage_device_ts / idx_switchbot_logs_device_ts / idx_device_records_device_ts
-- の3本だけで、いずれも先頭列が device_id である。保持期間削除の条件は
-- 「デバイスを問わず timestamp が境界より古い行」なので、先頭列が device_id の
-- 索引はこの検索には使えず、**毎晩テーブル全体をスキャンしながら書き込みロックを
-- 保持する**ことになる。保持期間削除を入れること自体が
-- 「長い書き込みロック → 同時に走る Webhook の保存が database is locked の
-- リトライ上限を超える → センサーイベントが恒久的に失われる」という、
-- #733 が問題の連鎖として挙げた経路を踏むことになってしまう。
--
-- 同じ理由で、ダッシュボードの `ORDER BY timestamp DESC LIMIT n`
-- (services/analysis_service.py の load_sensor_data / load_generic_data 等)も
-- 既存の索引を使えずフルスキャン + ソートになっていた。この索引はそちらにも効く
-- (Issue #741 が「推奨修正」として挙げていた (timestamp) 単独索引にあたる)。
--
-- 対象は db_retention_service.RETENTION_TARGETS に登録したテーブルに限っている。
-- tests/test_db_retention_service.py が「登録された全テーブルの DELETE が
-- EXPLAIN QUERY PLAN で索引を使うこと」を検証し、対象を増やしたのに索引を
-- 足し忘れる退行を検知する。

CREATE INDEX IF NOT EXISTS idx_device_records_ts
    ON device_records (timestamp);

CREATE INDEX IF NOT EXISTS idx_power_usage_ts
    ON power_usage (timestamp);

CREATE INDEX IF NOT EXISTS idx_switchbot_meter_logs_ts
    ON switchbot_meter_logs (timestamp);

CREATE INDEX IF NOT EXISTS idx_nas_records_ts
    ON nas_records (timestamp);

CREATE INDEX IF NOT EXISTS idx_bicycle_parking_records_ts
    ON bicycle_parking_records (timestamp);

CREATE INDEX IF NOT EXISTS idx_security_logs_ts
    ON security_logs (timestamp);

-- routine_step_events の既存索引 idx_routine_step_events_user_date は
-- (user_id, progress_date) で、保持期間削除が見る occurred_at とは別の列。
CREATE INDEX IF NOT EXISTS idx_routine_step_events_occurred
    ON routine_step_events (occurred_at);
