-- UI/UX改善のための画面タップログ(ファミクエ本体のみ。カメラ画面は対象外)。
--
-- 背景: 「いつ・どのボタンや要素を・どの画面で」タップしたかを蓄積し、押しにくい場所・
-- 反応しない場所・迷いやすい導線を後から分析できるようにする。クライアントは
-- 1タップごとに送らず、キューにためてバッチ(最大100件)で POST /api/ui-log/events へ
-- 送る(services/ui_event_service.py)。
--
-- 追記専用のため routine_step_events(0011)と同じ形にしている。UPDATEは行わない。
-- event_id はクライアントが採番するUUIDで、再送(オフライン復帰・sendBeaconの重複)で
-- 同じタップが二重に記録されないよう UNIQUE にしている(INSERT OR IGNORE)。
--
-- 行数の見積り(実測ではなく仮定): 家族4人が1日100タップで年15万行・15MB前後。
-- db_retention_service.RETENTION_TARGETS に登録し、routine_step_events と同じ
-- DB_ROW_RETENTION_EVENT_DAYS(既定400日)で削除対象にする(削除は既存方式どおり
-- 既定ドライラン)。
CREATE TABLE IF NOT EXISTS ui_tap_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,   -- クライアント採番のUUID(冪等化用)
    user_id TEXT,                    -- 画面で選択中のユーザー。クライアント入力のまま(認可は行わない)
    session_id TEXT NOT NULL,        -- ページ読み込みごとのランダムID(1回の操作の流れを辿る用)
    occurred_at TEXT NOT NULL,       -- タップ時刻。ISO8601 + JSTオフセット (core.utils.get_now_iso と同形式)
    received_at TEXT NOT NULL,       -- サーバー受信時刻(端末の時計ずれ・オフライン遅延の確認用)
    screen TEXT NOT NULL,            -- 'quest' | 'shop' | 'inventory' | 'familyLog' など
    element_id TEXT,                 -- data-track > aria-label > 文言の先頭30文字(入力欄の値は記録しない)
    element_tag TEXT,                -- 'button' | 'a' | 'div' 等
    is_interactive INTEGER NOT NULL, -- 1: ボタン/リンク等の操作要素へのタップ, 0: 反応しない場所へのタップ
    x_pct REAL,                      -- 画面幅に対する位置(0〜100)
    y_pct REAL,                      -- 画面高さに対する位置(0〜100)
    layout_mode TEXT                 -- 'portrait' | 'landscape'
);

CREATE INDEX IF NOT EXISTS idx_ui_tap_events_occurred_at
    ON ui_tap_events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_ui_tap_events_user_occurred
    ON ui_tap_events(user_id, occurred_at);
