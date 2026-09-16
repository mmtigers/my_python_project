-- デイリールーティン(すごろく)のステップ状態遷移を「いつ起きたか」つきで残す追記専用テーブル。
--
-- 背景: routine_progress.steps_status は {step_key: "locked"|"current"|"done"|"remind"} の
-- 2値相当のJSONで、行全体の updated_at しか時刻を持たない。そのため「7:50の締切に
-- 間に合ったか」は残るが、「何時何分に着替えを終えたか」「着替えに何分かかったか」は
-- 毎日生成されては毎日失われていた。療育目的の生活動線(routine_data.py の冒頭コメント参照)
-- では達成の有無より所要時間の推移のほうが重要なため、時系列として残せるようにする。
--
-- なぜ steps_status を {status, at} のような構造へ拡張しないか:
-- routine_service の _eligible_done_ratio / _activate_block / _toggle_checklist_step /
-- _serialize_flow はいずれも steps_status の値を素の文字列として比較しており、値の形を
-- 変えると4関数すべてを同時に壊す。既存の読み取り経路を一切変更せずに時刻だけを足せる
-- 追記専用の別テーブルにする。
--
-- 行数の見積り: 1ユーザー1日あたり最大15行程度(am 6 + pm 10 に取り消し操作の分を加味)、
-- 家族4人で年間2万行程度にしかならないため、nas_monitor の保持期間削除のような
-- クリーンアップ対象には含めない(意図的に永続保持する。長期の推移そのものが価値のため)。
CREATE TABLE IF NOT EXISTS routine_step_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    flow_key TEXT NOT NULL,          -- 'am' | 'pm' (routine_data.ROUTINE_FLOWS のキー)
    progress_date TEXT NOT NULL,     -- 'YYYY-MM-DD' (JST)。routine_progress と同じ日付境界
    step_key TEXT NOT NULL,          -- routine_data.RoutineStep.key ('meal', 'clothes' 等)
    -- 遷移前の状態。行の新規作成に伴う記録(source='carryover_skip')では NULL になる。
    from_status TEXT,
    to_status TEXT NOT NULL,         -- 'locked' | 'current' | 'done' | 'remind'
    -- 誰がこの遷移を起こしたか。集計時に「自分でチェックした」のか
    -- 「締切超過で自動的にそうなった」のかを区別するために持つ(療育の分析上、
    -- 両者を同じ 'done' として扱うと所要時間の統計が壊れる)。
    --   'user'              : ユーザー操作(POST /api/routine/complete)による遷移
    --   'forced_transition' : チェックポイント時刻超過による強制遷移
    --   'carryover_skip'    : 土日スキップ/繰越により、当日は実施せず done 扱いになったもの
    source TEXT NOT NULL,
    occurred_at TEXT NOT NULL        -- ISO8601 + JSTオフセット (core.utils.get_now_iso と同形式)
);

CREATE INDEX IF NOT EXISTS idx_routine_step_events_user_date
    ON routine_step_events(user_id, progress_date);
