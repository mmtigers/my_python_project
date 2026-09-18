-- デイリールーティン(すごろく形式の生活導線UI)の進捗を保持するテーブル。
-- ルーティン自体の定義(どのステップがあるか等)はユーザー編集の対象ではなく
-- 常に固定のため、quest_master のようなDBテーブルにはせず routine_data.py の
-- 定数として持つ(Issue #330の精神: 可変なものだけをDBに置く)。ここではユーザー×
-- フロー(am/pm)×日付ごとの「今どのステップにいるか」だけを保持する。
CREATE TABLE IF NOT EXISTS routine_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    flow_key TEXT NOT NULL,
    progress_date TEXT NOT NULL, -- 'YYYY-MM-DD' (JST)
    current_step_index INTEGER NOT NULL DEFAULT 0,
    in_free_time INTEGER NOT NULL DEFAULT 0,
    -- {"顔を洗う"等のstep_key: "locked"|"current"|"done"|"remind"} のJSON文字列。
    -- ステップ数が少なく(最大6件/フロー)、進捗の可視化以外の用途で個別ステップを
    -- 検索する必要が無いため、正規化した別テーブルにはせずJSONで持つ。
    steps_status TEXT NOT NULL DEFAULT '{}',
    bonus_gold INTEGER NOT NULL DEFAULT 0,
    bonus_exp INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, flow_key, progress_date)
);

CREATE INDEX IF NOT EXISTS idx_routine_progress_user_date
    ON routine_progress(user_id, progress_date);
