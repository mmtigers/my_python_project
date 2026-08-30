-- weather_history に location/max_pop/umbrella_level カラムを追加する。
-- init_unified_db.py のCREATE TABLE定義がcurrent_schema.sql(実運用スキーマ)と乖離しており、
-- 新規DB(init_db)では services/analysis_service.py がSELECTするlocation/umbrella_level列が
-- 存在せず、OperationalErrorが握りつぶされて天気関連の表示・年間気温統計が無言で空になっていた。
ALTER TABLE weather_history ADD COLUMN location TEXT DEFAULT '伊丹';
ALTER TABLE weather_history ADD COLUMN max_pop INTEGER;
ALTER TABLE weather_history ADD COLUMN umbrella_level TEXT;
