-- Issue #329: quest_master.reset_period のカラムDEFAULTが 'weekly_monday'
-- (is_within_reset_period() が扱えない未対応値) のまま残っている問題の根治。
-- 既存行のデータは 0005 で 'daily' へ補正済みだが、カラムDEFAULT自体は
-- SQLiteの ALTER TABLE では変更できないため、テーブル再作成方式で 'daily' に修正する。
--
-- 手順:
--   1. 環境によりカラム構成が異なるDB(init_unified_db.py由来の新規DBには days と
--      reset_period が無い等)を、ADD COLUMN群で本番相当のフルカラム構成へ正規化する。
--      既に列が存在するDBでは duplicate column となるが、ランナー(core/migrations.py)が
--      「別経路で適用済み」として警告ログのみでスキップし後続文の実行を継続する。
--   2. 正しいDEFAULTを持つ新テーブルへ全行コピーし、テーブルを入れ替える。
--      quest_master を参照する外部キー・インデックス・トリガーは存在しないため、
--      再作成に伴う付随オブジェクトの復元は不要。
ALTER TABLE quest_master ADD COLUMN description TEXT;
ALTER TABLE quest_master ADD COLUMN quest_type TEXT DEFAULT 'daily';
ALTER TABLE quest_master ADD COLUMN exp_gain INTEGER DEFAULT 10;
ALTER TABLE quest_master ADD COLUMN gold_gain INTEGER DEFAULT 5;
ALTER TABLE quest_master ADD COLUMN icon_key TEXT;
ALTER TABLE quest_master ADD COLUMN day_of_week TEXT;
ALTER TABLE quest_master ADD COLUMN target_user TEXT DEFAULT 'all';
ALTER TABLE quest_master ADD COLUMN start_date TEXT;
ALTER TABLE quest_master ADD COLUMN end_date TEXT;
ALTER TABLE quest_master ADD COLUMN occurrence_chance REAL DEFAULT 1.0;
ALTER TABLE quest_master ADD COLUMN start_time TEXT;
ALTER TABLE quest_master ADD COLUMN end_time TEXT;
ALTER TABLE quest_master ADD COLUMN days TEXT;
ALTER TABLE quest_master ADD COLUMN pre_requisite_quest_id INTEGER DEFAULT NULL;
ALTER TABLE quest_master ADD COLUMN reset_period TEXT DEFAULT 'daily';
-- 過去の異常終了等で作業用テーブルが残っていた場合に備えて先に除去する(再実行安全)
DROP TABLE IF EXISTS quest_master_rebuild_0008;
CREATE TABLE quest_master_rebuild_0008 (
        quest_id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT,
        quest_type TEXT DEFAULT 'daily',
        exp_gain INTEGER DEFAULT 10,
        gold_gain INTEGER DEFAULT 5,
        icon_key TEXT,
        day_of_week TEXT,
        target_user TEXT DEFAULT 'all',
        start_date TEXT,
        end_date TEXT,
        occurrence_chance REAL DEFAULT 1.0,
        start_time TEXT,
        end_time TEXT,
        days TEXT,
        pre_requisite_quest_id INTEGER DEFAULT NULL,
        reset_period TEXT DEFAULT 'daily'
);
-- INSERTが暗黙トランザクションを開始するため、後続のDROP/RENAMEを含めて
-- 失敗時はロールバックされ、元の quest_master が失われることはない
INSERT INTO quest_master_rebuild_0008 (
        quest_id, title, description, quest_type, exp_gain, gold_gain,
        icon_key, day_of_week, target_user, start_date, end_date,
        occurrence_chance, start_time, end_time, days,
        pre_requisite_quest_id, reset_period
)
SELECT  quest_id, title, description, quest_type, exp_gain, gold_gain,
        icon_key, day_of_week, target_user, start_date, end_date,
        occurrence_chance, start_time, end_time, days,
        pre_requisite_quest_id, reset_period
FROM quest_master;
DROP TABLE quest_master;
ALTER TABLE quest_master_rebuild_0008 RENAME TO quest_master;
-- 0005以降にDEFAULT経由で 'weekly_monday' が入った行が万一残っていた場合の最終補正
UPDATE quest_master SET reset_period = 'daily' WHERE reset_period IS NULL OR reset_period = 'weekly_monday';
