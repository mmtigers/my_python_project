-- Issue #746 (AUDIT-017) / Issue #507 の実機側の積み残し:
-- 既に使われていないテーブルのうち、**行を1つも持たないもの**を実機DBから落とす。
--
-- 2026-09-20 に実機(Raspberry Pi)で全件の行数を確認した結果にもとづく。
-- 行を持つテーブルは DROP がデータの破棄になるため、本マイグレーションでは触らない。
--
-- ■ #746(AUDIT-017): ベースラインにある退役済みテーブル
--   users:  0 行  ← quest_users の旧版
--   quests: 0 行  ← quest_master の旧版
--
--   この2つは現行テーブルと名前が紛らわしいこと自体がリスクだった。新しく
--   このコードベースに触る人(将来の自分・AIエージェントを含む)が「ユーザーテーブルは
--   users だろう」と推測して `INSERT INTO users (...)` と書いても SQLite はエラーに
--   しないため、データがサイレントに行方不明になる。
--
--   残り7件(party_state 1行 / equipment_master 16行 / user_equipments 12行 /
--   family_mileage 1行 / family_mileage_history 5行 / bounties 9行 /
--   suumo_records 56行)は行を持つため対象外。README の一覧に残す。
--
-- ■ #507: migrations/ に存在しないのに実機DBにだけ残っていた死蔵テーブル
--   quest_tasks:           0 行
--   quest_status:          0 行
--   youtube_subscriptions: 0 行
--
--   #507 はこれらをリポジトリのスキーマ定義から外したが、**既存DBから落とす経路が
--   無かった**ため実機には残り続けていた(リポジトリ上は存在しない = 誰も気づけない)。
--
--   とくに quest_tasks は実害があった。定義が
--       FOREIGN KEY (target_user_id) REFERENCES quest_users(id)
--   となっているが、quest_users の主キーは `user_id TEXT` で **`id` 列は存在しない**。
--   この不整合により:
--     1. quest_tasks への INSERT は `PRAGMA foreign_keys=ON`(core/database.py が常に設定)
--        の下で必ず `OperationalError: foreign key mismatch` になる
--     2. **`PRAGMA foreign_key_check` が DB 全体で実行不能になる**(このテーブル1つで
--        例外になるため、他テーブルの参照整合性も確認できない)
--   2 は Issue #747(FK 追加)の前提を潰していた。削除により foreign_key_check が
--   使えるようになる。
--
--   haircut_history(1行)・app_rankings(211行)は行を持つため対象外。
--
-- 再実行安全性: すべて IF EXISTS を付けているため、既に消えているDBに対して
-- 再実行してもエラーにならない(ランナーの「already exists」スキップに頼らない)。

DROP TABLE IF EXISTS users;

DROP TABLE IF EXISTS quests;

DROP TABLE IF EXISTS quest_tasks;

DROP TABLE IF EXISTS quest_status;

DROP TABLE IF EXISTS youtube_subscriptions;
