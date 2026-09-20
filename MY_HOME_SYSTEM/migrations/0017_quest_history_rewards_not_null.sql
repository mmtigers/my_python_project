-- Issue #747 (REPOSITORY_AUDIT_2026-09-18 の AUDIT-018) のステップ2:
-- quest_history の報酬列を `DEFAULT 0 NOT NULL` にする。
--
-- これらは NULL 許容のままだったため、サービス層が「サービス外で挿入された行を
-- 承認すると user['gold'] + None の TypeError → 500 になる」という防御を
-- 個別に持っていた(services/quest/approval_service.py、services/quest/rewards.py)。
-- 列の制約で保証すれば、この種の防御が不要になる。
--
-- 2026-09-20 の実機確認では gold_earned / exp_earned / medals_earned とも
-- NULL は 0 件(全 3,408 行)。したがって本マイグレーションは既存データを変えず、
-- 「今後 NULL を入れられなくする」ための制約強化のみである。
-- 念のため INSERT 時に COALESCE(..., 0) を通しており、NULL を含むDBでも 0 に寄せて移行する。
--
-- SQLite は ALTER TABLE で既存列に NOT NULL を追加できないため、公式手順
-- (新テーブル作成 → データコピー → 旧テーブル DROP → RENAME)を踏む。
-- ランナー(core/migrations.py)はマイグレーション1ファイルを1トランザクションで実行し、
-- OperationalError 時は rollback してバージョンを記録しないため、途中で失敗しても
-- 中途半端なスキーマは残らない。
--
-- 列の順序について: 実機のDB(ALTER TABLE を重ねた結果)とベースラインから作った
-- 新規DBとでは列の物理順が異なるため、コピーは SELECT * ではなく**列名を明示**して
-- 行う。本マイグレーション適用後はどちらの経路でも下記の順序に揃う。
--
-- quest_history は他テーブルから外部キーで参照されていない(FK は
-- user_inventory.reward_id の1つだけ)ため、DROP/RENAME による参照の壊れは生じない。
-- linked_history_id の自己参照も FK 宣言ではないので影響しない。

-- 先に「コピー元に列が必ず存在する」状態を作る。
-- 実機・ベースライン経由のDBでは3列とも既に存在し、ランナーが duplicate column を
-- 既知エラーとしてスキップする(README の「ALTER TABLE ... ADD COLUMN を先頭に書く」規約)。
-- ベースライン以前の旧スキーマDB(tests/test_migrations.py が再現しているもの)では
-- quest_history が id/user_id/quest_id/status/completed_at しか持たないため、
-- ここで補完しないと下の INSERT ... SELECT が "no such column" で落ちる。
ALTER TABLE quest_history ADD COLUMN quest_title TEXT;

ALTER TABLE quest_history ADD COLUMN exp_earned INTEGER;

ALTER TABLE quest_history ADD COLUMN gold_earned INTEGER;

CREATE TABLE quest_history_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    quest_id INTEGER,
    quest_title TEXT,
    status TEXT DEFAULT 'approved',
    completed_at DATETIME NOT NULL,
    exp_earned INTEGER NOT NULL DEFAULT 0,
    gold_earned INTEGER NOT NULL DEFAULT 0,
    medals_earned INTEGER NOT NULL DEFAULT 0,
    linked_history_id INTEGER DEFAULT NULL
);

INSERT INTO quest_history_new (
    id, user_id, quest_id, quest_title, status, completed_at,
    exp_earned, gold_earned, medals_earned, linked_history_id
)
SELECT
    id, user_id, quest_id, quest_title, status, completed_at,
    COALESCE(exp_earned, 0), COALESCE(gold_earned, 0), COALESCE(medals_earned, 0),
    linked_history_id
FROM quest_history;

DROP TABLE quest_history;

ALTER TABLE quest_history_new RENAME TO quest_history;

-- 索引は DROP TABLE で一緒に消えるため、0013 と同じ定義で張り直す
-- (定義を変えると tests/test_db_indexes.py の被覆索引の回帰テストが落ちる)。
CREATE INDEX IF NOT EXISTS idx_quest_history_user_quest_completed
    ON quest_history (user_id, quest_id, completed_at DESC, status);

CREATE INDEX IF NOT EXISTS idx_quest_history_status_completed
    ON quest_history (status, completed_at DESC);
