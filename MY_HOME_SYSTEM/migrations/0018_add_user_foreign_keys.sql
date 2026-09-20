-- Issue #747 (REPOSITORY_AUDIT_2026-09-18 の AUDIT-018) のステップ3:
-- 「子テーブル → quest_users」の参照を外部キーで担保する。
--
-- ■ 背景
-- `core/database.py` は常に `PRAGMA foreign_keys=ON` を設定しているのに、外部キー宣言は
-- `user_inventory.reward_id` の1つしか無かった。その結果「本来DBが防げる不整合」への
-- 防御がサービス層に散在し(承認時の `if not user: return None` 等)、同じ種類のバグが
-- 別々の Issue で繰り返し個別に塞がれてきた(#370 / #409 / #550 系)。
--
-- ■ 対象の選定(2026-09-20 に実機データで確定)
-- 候補11関係すべての孤児行を実測し、**張ってよいものだけ**を対象にした。
--
-- 張る(いずれも孤児0件。ユーザー行が通常運用で消えることはなく、消えたら異常):
--   quest_history.user_id             -> quest_users.user_id
--   reward_history.user_id            -> quest_users.user_id
--   user_inventory.user_id            -> quest_users.user_id
--   routine_progress.user_id          -> quest_users.user_id
--   routine_step_events.user_id       -> quest_users.user_id
--   quest_cancellation_audit.user_id  -> quest_users.user_id
--   (user_inventory.reward_id -> reward_master.reward_id は既存。再作成後も維持する)
--
-- 張らない(いずれも「参照先が消えても子を残す」ことが設計意図であり、FKと矛盾する):
--   quest_history.quest_id  -> quest_master   … 実機に1,219行の「孤児」。sync_master_data が
--       退役クエストをマスタから消しても、quest_history は quest_title を非正規化保持して
--       履歴を残す設計。RESTRICT では退役が永久にできなくなり、CASCADE では家族の記録が
--       消える(#762 と衝突)。
--   reward_history.reward_id -> reward_master … 同上(実機1行)。
--   quest_cancellation_audit.history_id -> quest_history … **監査行は削除された履歴の
--       控えそのもの**。approval_service は監査行を書いてから quest_history を DELETE する
--       ため、FK を張ると取消フローがその場で壊れる。
--   quest_history.linked_history_id -> quest_history (自己参照) … 兄妹連携の履歴は
--       **相互に**指し合う(実機で 3632 <-> 3633 を確認)。取消は主履歴 → 相方の順に
--       削除するため、RESTRICT では最初の DELETE が相方からの参照で失敗する。
--
-- ■ ON DELETE の方針
-- すべて既定(NO ACTION = 参照が残っている限り親の削除を許さない)にする。
-- `quest_users` の行を消す経路はリポジトリ内に存在せず(admin/reset_user も
-- 残高をゼロ化するだけで行は消さない)、消えたとすれば手動SQL等の異常な操作である。
-- その場合に「静かに孤児を作る」のではなく「その場で失敗する」のが本 Issue の狙い。
--
-- ■ 手順
-- SQLite は ALTER TABLE で FK を追加できないため、テーブルごとに
-- 新テーブル作成 → コピー → DROP → RENAME → 索引再作成 を行う。
-- ランナー(core/migrations.py)は1ファイルを1トランザクションで実行し、失敗時は
-- rollback してバージョンを記録しないため、途中で失敗しても中途半端な状態は残らない。
-- コピーは列名を明示し、INSERT 時に FK が検証される(= 移行そのものが整合性チェックになる)。

-- ============================================================
-- quest_history
-- ============================================================
CREATE TABLE quest_history_fk (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    quest_id INTEGER,
    quest_title TEXT,
    status TEXT DEFAULT 'approved',
    completed_at DATETIME NOT NULL,
    exp_earned INTEGER NOT NULL DEFAULT 0,
    gold_earned INTEGER NOT NULL DEFAULT 0,
    medals_earned INTEGER NOT NULL DEFAULT 0,
    linked_history_id INTEGER DEFAULT NULL,
    FOREIGN KEY (user_id) REFERENCES quest_users(user_id)
);

INSERT INTO quest_history_fk (
    id, user_id, quest_id, quest_title, status, completed_at,
    exp_earned, gold_earned, medals_earned, linked_history_id
)
SELECT
    id, user_id, quest_id, quest_title, status, completed_at,
    exp_earned, gold_earned, medals_earned, linked_history_id
FROM quest_history;

DROP TABLE quest_history;

ALTER TABLE quest_history_fk RENAME TO quest_history;

CREATE INDEX IF NOT EXISTS idx_quest_history_user_quest_completed
    ON quest_history (user_id, quest_id, completed_at DESC, status);

CREATE INDEX IF NOT EXISTS idx_quest_history_status_completed
    ON quest_history (status, completed_at DESC);

-- ============================================================
-- reward_history
-- ============================================================
CREATE TABLE reward_history_fk (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    reward_id INTEGER,
    reward_title TEXT,
    cost_gold INTEGER,
    redeemed_at DATETIME NOT NULL,
    FOREIGN KEY (user_id) REFERENCES quest_users(user_id)
);

INSERT INTO reward_history_fk (id, user_id, reward_id, reward_title, cost_gold, redeemed_at)
SELECT id, user_id, reward_id, reward_title, cost_gold, redeemed_at FROM reward_history;

DROP TABLE reward_history;

ALTER TABLE reward_history_fk RENAME TO reward_history;

CREATE INDEX IF NOT EXISTS idx_reward_history_user_reward_redeemed
    ON reward_history (user_id, reward_id, redeemed_at DESC);

-- ============================================================
-- user_inventory (reward_id への既存FKを維持したまま user_id のFKを足す)
-- ============================================================
CREATE TABLE user_inventory_fk (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    reward_id INTEGER,
    status TEXT DEFAULT 'owned',
    purchased_at DATETIME NOT NULL,
    used_at DATETIME,
    FOREIGN KEY (user_id) REFERENCES quest_users(user_id),
    FOREIGN KEY (reward_id) REFERENCES reward_master(reward_id)
);

INSERT INTO user_inventory_fk (id, user_id, reward_id, status, purchased_at, used_at)
SELECT id, user_id, reward_id, status, purchased_at, used_at FROM user_inventory;

DROP TABLE user_inventory;

ALTER TABLE user_inventory_fk RENAME TO user_inventory;

CREATE INDEX IF NOT EXISTS idx_user_inventory_user_status
    ON user_inventory (user_id, status);

CREATE INDEX IF NOT EXISTS idx_user_inventory_user_reward_used
    ON user_inventory (user_id, reward_id, used_at DESC);

-- ============================================================
-- routine_progress (UNIQUE(user_id, flow_key, progress_date) を維持)
-- ============================================================
CREATE TABLE routine_progress_fk (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    flow_key TEXT NOT NULL,
    progress_date TEXT NOT NULL,
    current_step_index INTEGER NOT NULL DEFAULT 0,
    in_free_time INTEGER NOT NULL DEFAULT 0,
    steps_status TEXT NOT NULL DEFAULT '{}',
    bonus_gold INTEGER NOT NULL DEFAULT 0,
    bonus_exp INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    skipped_keys TEXT NOT NULL DEFAULT '[]',
    UNIQUE(user_id, flow_key, progress_date),
    FOREIGN KEY (user_id) REFERENCES quest_users(user_id)
);

INSERT INTO routine_progress_fk (
    id, user_id, flow_key, progress_date, current_step_index, in_free_time,
    steps_status, bonus_gold, bonus_exp, created_at, updated_at, skipped_keys
)
SELECT
    id, user_id, flow_key, progress_date, current_step_index, in_free_time,
    steps_status, bonus_gold, bonus_exp, created_at, updated_at, skipped_keys
FROM routine_progress;

DROP TABLE routine_progress;

ALTER TABLE routine_progress_fk RENAME TO routine_progress;

CREATE INDEX IF NOT EXISTS idx_routine_progress_user_date
    ON routine_progress(user_id, progress_date);

-- ============================================================
-- routine_step_events
-- ============================================================
CREATE TABLE routine_step_events_fk (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    flow_key TEXT NOT NULL,
    progress_date TEXT NOT NULL,
    step_key TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    source TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES quest_users(user_id)
);

INSERT INTO routine_step_events_fk (
    id, user_id, flow_key, progress_date, step_key, from_status, to_status, source, occurred_at
)
SELECT
    id, user_id, flow_key, progress_date, step_key, from_status, to_status, source, occurred_at
FROM routine_step_events;

DROP TABLE routine_step_events;

ALTER TABLE routine_step_events_fk RENAME TO routine_step_events;

CREATE INDEX IF NOT EXISTS idx_routine_step_events_user_date
    ON routine_step_events(user_id, progress_date);

CREATE INDEX IF NOT EXISTS idx_routine_step_events_occurred
    ON routine_step_events(occurred_at);

-- ============================================================
-- quest_cancellation_audit
--   history_id には FK を張らない(削除された履歴を指すのが役割のため)。
-- ============================================================
CREATE TABLE quest_cancellation_audit_fk (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    history_id INTEGER NOT NULL,
    user_id TEXT,
    cancelled_by TEXT NOT NULL,
    quest_id INTEGER,
    quest_title TEXT,
    status_before TEXT,
    completed_at DATETIME,
    exp_earned INTEGER,
    gold_earned INTEGER,
    medals_earned INTEGER,
    linked_history_id INTEGER,
    cascaded INTEGER NOT NULL DEFAULT 0,
    rewards_reverted INTEGER NOT NULL DEFAULT 0,
    cancelled_at DATETIME NOT NULL,
    FOREIGN KEY (user_id) REFERENCES quest_users(user_id)
);

INSERT INTO quest_cancellation_audit_fk (
    id, history_id, user_id, cancelled_by, quest_id, quest_title, status_before,
    completed_at, exp_earned, gold_earned, medals_earned, linked_history_id,
    cascaded, rewards_reverted, cancelled_at
)
SELECT
    id, history_id, user_id, cancelled_by, quest_id, quest_title, status_before,
    completed_at, exp_earned, gold_earned, medals_earned, linked_history_id,
    cascaded, rewards_reverted, cancelled_at
FROM quest_cancellation_audit;

DROP TABLE quest_cancellation_audit;

ALTER TABLE quest_cancellation_audit_fk RENAME TO quest_cancellation_audit;

CREATE INDEX IF NOT EXISTS idx_quest_cancellation_audit_history
    ON quest_cancellation_audit (history_id);

CREATE INDEX IF NOT EXISTS idx_quest_cancellation_audit_user_time
    ON quest_cancellation_audit (user_id, cancelled_at DESC);
