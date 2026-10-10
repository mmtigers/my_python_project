-- サーバー(unified_server)の起動履歴。システムページの「起動履歴」と、
-- 「起動中のコードが手元の最新に追いついているか」の判定に使う。
--
-- 背景: これまで再起動の履歴はどこにも残らず、「いつ再起動したか」「そのときどのコミットを
-- 動かしていたか」を後から確認できなかった(「最新に更新して再起動」の結果も1件だけ上書き保存)。
-- unified_server が lifespan の起動時に1行追記する(services/system_info_service.record_boot)。
-- 追記専用で UPDATE は行わない。異常終了(電源断・OOM等)は記録できないため、
-- 「起動した時刻」の履歴であって「停止した時刻」の履歴ではない。
--
-- reason: 'update'(ダッシュボードの「最新に更新して再起動」) / 'manual'(ダッシュボードの「システム再起動」)
--         / 'unknown'(上記以外。自動復旧・電源投入・cron等)
--
-- 行数は再起動1回につき1行(多くても1日数行)。db_retention_service.RETENTION_TARGETS に
-- 登録し、DB_ROW_RETENTION_EVENT_DAYS(既定400日)で削除対象にする(削除は既存方式どおり既定ドライラン)。
CREATE TABLE IF NOT EXISTS server_boot_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booted_at TEXT NOT NULL,      -- 起動時刻。ISO8601 + JSTオフセット (core.utils.get_now_iso と同形式)
    commit_sha TEXT,              -- 起動時点の git HEAD(短縮ハッシュ)。git が使えなければ NULL
    commit_subject TEXT,          -- そのコミットの件名
    branch TEXT,                  -- 起動時点のブランチ名
    pid INTEGER,                  -- unified_server のプロセスID
    reason TEXT NOT NULL DEFAULT 'unknown'
);

CREATE INDEX IF NOT EXISTS idx_server_boot_events_booted_at
    ON server_boot_events(booted_at);
