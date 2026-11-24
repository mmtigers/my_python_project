-- 駐車場カメラの録画から判定した自家用車の在否(#1116)。
--
-- monitors/parking_occupancy_monitor.py が10分ごとに、書き終わった駐車場カメラの録画を
-- 1本ずつ読み、1分ごとのフレームの特徴量を parking_observations に追記する。
-- 在否(あり/なし/不明)は保存しない。読み出すたびに特徴量から今のしきい値で判定し直す
-- (services/parking_record_service.py)。しきい値を見直しても録画を読み直さずに済むようにするため。
--
-- 時刻はファイル名と同じホストのローカル時刻("YYYY-MM-DD HH:MM:SS"。実機は JST)。
-- 録画ファイル名(nvr-parking.service の ffmpeg -strftime)と突き合わせるため。
--
-- 行数は1日あたり観測 約1,440行・録画 約144行。db_retention_service.RETENTION_TARGETS に
-- 登録し、DB_ROW_RETENTION_SENSOR_DAYS で削除対象にする(削除は既存方式どおり既定ドライラン)。

-- 解析した録画。ここに載っている録画は二度と読まない。
-- status: 'ok'(解析できた。観測は parking_observations) / 'unreadable'(ffmpeg 自身が読めないと
--         答えた。映像が1コマも入っていない録画など。何度読み直しても同じなので記録して飛ばす)
CREATE TABLE IF NOT EXISTS parking_recordings (
    file TEXT PRIMARY KEY,                 -- 録画ファイル名(例: 20261124_093023.mp4)
    started_at TEXT NOT NULL,              -- 録画の開始時刻(ファイル名から)
    status TEXT NOT NULL,
    observation_count INTEGER NOT NULL DEFAULT 0,
    processed_at TEXT NOT NULL             -- 解析した時刻
);

CREATE INDEX IF NOT EXISTS idx_parking_recordings_started_at
    ON parking_recordings(started_at);

-- 1フレームごとの特徴量(services/parking_occupancy_service.ParkingFeatures と同じ列)。
CREATE TABLE IF NOT EXISTS parking_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    file TEXT NOT NULL,                    -- 元の録画(parking_recordings.file)
    is_ir INTEGER NOT NULL,                -- 1 = 夜(IR・モノクロ)
    saturation REAL NOT NULL,
    flagstone_edge REAL NOT NULL,
    flagstone_lap_var REAL NOT NULL,
    floor_mean REAL NOT NULL,
    floor_std REAL NOT NULL,
    floor_edge REAL NOT NULL,
    floor_outlier REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_parking_observations_observed_at
    ON parking_observations(observed_at);
