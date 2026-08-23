-- device_records に nas_usage_percent カラムを追加する。
-- monitors/nas_monitor.py がNASのディスク使用率(%)を、電池残量用に後付けされた
-- battery_level カラムへ誤って流用していたため、専用カラムを新設して分離する。
-- 過去に battery_level へ書き込まれた行はそのまま残し、以後の書き込み先のみ切り替える。
ALTER TABLE device_records ADD COLUMN nas_usage_percent REAL;
