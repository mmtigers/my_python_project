# migrations/

スキーマ変更用のバージョン管理されたマイグレーションファイル置き場です。
`core/migrations.py` の `apply_pending_migrations()` が、このディレクトリ内の
`*.sql` をファイル名の昇順で読み込み、`schema_migrations` テーブルに記録された
適用済みバージョンと突き合わせて未適用分のみを実行します。

**このディレクトリがスキーマの唯一の定義元です (Issue #330)。**
`0000_baseline_schema.sql` が全テーブル・インデックスのベースライン
(全文 `CREATE TABLE IF NOT EXISTS` のため既存DBではno-op)、0001以降が
カラム追加・データ移行の積み上げです。以前 `init_unified_db.py` が持っていた
CREATE TABLE群は0000へ移設済みで、`init_db()` は本ディレクトリを適用するだけの
薄いラッパーになっています。空DBに `apply_pending_migrations()` だけを適用しても
フルスキーマが構築できることは `tests/test_empty_db_e2e.py` が
(旧init_dbスキーマのスナップショット `tests/fixtures/legacy_init_db_schema.json`
との突き合わせ込みで)検証しています。

## 退役済みテーブル・重複列 (Issue #746 / AUDIT-017)

ベースライン(`0000_baseline_schema.sql`)には、**2026年8月のリファクタリング
(ボス戦・装備・ギルド・マイレージ・週間ランキングの削除)で使われなくなったテーブルが
そのまま残っている**。以下はいずれも**実行コードから一切参照されていない死蔵テーブルであり、
新しいコードから読み書きしてはならない**。

### 残しているもの(実機で行を持つため)

DROP は不可逆でデータの破棄になるため、**意図的に残している**。行数は 2026-09-20 の実機実測。

| 退役済みテーブル | 行数 | 備考 |
| --- | ---: | --- |
| `party_state` | 1 | ボス戦 |
| `equipment_master` / `user_equipments` | 16 / 12 | 装備 |
| `family_mileage` / `family_mileage_history` | 1 / 5 | マイレージ |
| `bounties` | 9 | 賞金クエスト |
| `suumo_records` | 56 | 物件情報収集 |
| `haircut_history` | 1 | Issue #507 でスキーマ定義から外したが実機に残存 |
| `app_rankings` | 211 | 同上(ダッシュボードの「📊 トレンド」タブの書き込み側が退役済み) |

### DROP 済み(`0016_drop_retired_users_and_quests_tables.sql`)

実機で **0 行**であることを確認したうえで落とした。

| テーブル | 経緯 |
| --- | --- |
| `users` | `quest_users` の旧版。名前が紛らわしく誤書き込みのリスクが最も高かった(#746) |
| `quests` | `quest_master` の旧版。同上(#746) |
| `quest_tasks` | #507 の死蔵テーブル。`REFERENCES quest_users(id)` と宣言されているが `quest_users` の主キーは `user_id TEXT` で **`id` 列は存在しない**。このため INSERT が必ず `foreign key mismatch` になるうえ、**`PRAGMA foreign_key_check` が DB 全体で実行不能**になっており、Issue #747(FK 追加)の前提を潰していた |
| `quest_status` | #507 の死蔵テーブル |
| `youtube_subscriptions` | #507 の死蔵テーブル |

> Issue #507 はこれらをリポジトリのスキーマ定義から外したが、**既存DBから落とす経路が
> 無かった**ため実機には残り続けていた。リポジトリ上に存在しない以上、誰も気づけない
> 状態だった。同種の「定義から外す」変更を行う際は、既存DBへの DROP マイグレーションも
> セットで用意すること。

> DROP したテーブルは `init_unified_db.validate_schema_integrity` の `expected_schemas`
> からも外すこと。残したままだと起動のたびに「Missing Table」の警告が出る。

## 外部キーの方針 (Issue #747 / AUDIT-018)

`core/database.py` は常に `PRAGMA foreign_keys=ON` を設定している。`0018_add_user_foreign_keys.sql`
で「子テーブル → `quest_users`」の6関係に外部キーを張った（`ON DELETE` は既定の NO ACTION）。

| 張っている | 備考 |
| --- | --- |
| `quest_history.user_id` / `reward_history.user_id` / `user_inventory.user_id` / `routine_progress.user_id` / `routine_step_events.user_id` / `quest_cancellation_audit.user_id` → `quest_users.user_id` | ユーザー行が通常運用で消えることはない（`admin/reset_user` も残高をゼロ化するだけで行は消さない）。消えたとすれば異常なので、静かに孤児を作らずその場で失敗させる |
| `user_inventory.reward_id` → `reward_master.reward_id` | 従来からある唯一のFK。`sync_master_data` は所持者がいる報酬の削除を `IntegrityError` で検知してスキップする |

**意図的に張っていない関係**（いずれも「参照先が消えても子を残す」ことが設計意図であり、FK と矛盾する）:

| 関係 | 理由 |
| --- | --- |
| `quest_history.quest_id` → `quest_master` | `sync_master_data` が退役クエストをマスタから消しても、`quest_title` を非正規化保持して履歴を残す設計。RESTRICT では退役が永久にできなくなり、CASCADE では家族の記録が消える |
| `reward_history.reward_id` → `reward_master` | 同上 |
| `quest_cancellation_audit.history_id` → `quest_history` | 監査行は**削除された履歴の控えそのもの**。`approval_service` は監査行を書いてから履歴を DELETE するため、FK を張ると取消フローがその場で壊れる |
| `quest_history.linked_history_id` → `quest_history`（自己参照） | 兄妹連携の履歴は**相互に**指し合う。取消は主履歴 → 相方の順に削除するため、RESTRICT では最初の DELETE が相方からの参照で失敗する |

この「張らない」判断は `monitors/health_watch.py` のチェック11 の2層構成（`_ORPHAN_CHECKS_STRICT` は通知、`_ORPHAN_CHECKS_EXPECTED` は記録のみ）と対応している。

同じく使われていない重複列が2組ある。

| 列 | 実際に使う列 |
| --- | --- |
| `quest_master.days` | `quest_master.day_of_week` (`services/quest/quest_service.py` のコメント参照) |
| `reward_master.desc` | `reward_master.description` (`services/quest/game_system.py` が `desc` を落として返す) |

**`days`/`desc` は現行のものと名前が紛らわしく、誤って書き込んでも
SQLite はエラーにしない**(データがサイレントに行方不明になる)。この一覧と
「コードから参照されていないこと」は `tests/test_retired_tables_unused.py` が固定しており、
DROP する際はそのテストとこの節も同時に更新すること。

## 新しいマイグレーションの追加方法

1. `NNNN_short_description.sql` の形式でファイルを追加する（`NNNN` は既存の最大値+1のゼロ埋め4桁連番。`0000` はベースライン専用の予約番号で、以後使わない）。
   - 新しいテーブルの追加も、既存の `0000_baseline_schema.sql` を書き換えるのではなく、新しい `NNNN_*.sql` の `CREATE TABLE IF NOT EXISTS` として追加する。
2. 可能な限り `ALTER TABLE ... ADD COLUMN` を先頭に書き、後続のデータ移行(UPDATE等)はその後に続ける。
   - 既に列が存在する環境（過去の実行時チェックで先に適用済み等）に対して再実行された場合、
     `ALTER TABLE` の失敗（duplicate column）はランナー側で警告ログとして扱われ、
     処理は継続します。
3. 本番相当のダミーデータで一度動作確認してからコミットする。

## 実行タイミング

- `init_unified_db.init_db()`（テスト・初期セットアップ用）
- `unified_server.py` の起動時（`lifespan`）

のいずれからも呼び出されます。

## `../current_schema.sql` との関係 (Issue #411)

`MY_HOME_SYSTEM/current_schema.sql` は、実行時にはどこからも参照・実行されない
参考ドキュメントである。上記のとおり**このディレクトリ(`migrations/`)がスキーマの
唯一の定義元**であり、`current_schema.sql` はそれと矛盾したり実行時の挙動を決定したり
しない。

**`current_schema.sql` は生成物である**(2026-09 の自動化対応で位置づけを確定):
`init_unified_db.py --dump-schema` が、本ディレクトリの全マイグレーションを空の
`:memory:` DB へ適用し、`sqlite_master` の `CREATE` 文を作成順に書き出す。
`tests/test_current_schema_sql.py` が「コミット済みの内容 == 再生成結果」の完全一致を
検証するため、マイグレーションを追加・変更した PR では必ず再生成してコミットすること:

```bash
cd MY_HOME_SYSTEM && python init_unified_db.py --dump-schema
```

経緯: 以前は手書きの「あるべき姿」として維持され、`migrations/` 全適用のスキーマとの
既知差分(`CREATE INDEX` の欠落、`device_records.battery_level`・`food_records.date/menu/
created_at` などファイル側にしか無い列、`NOT NULL`・`DEFAULT` の相違。Issue #411/#543)を
README とテストの許容リストで管理していた。生成物化に伴いこれらの差分は解消され
(ファイル側にしか無かった列・制約は、`migrations/` に存在しない以上 実 DB にも存在しない
ため削除した)、許容リストも撤去した。Issue #507 で削除した死蔵テーブル
(`haircut_history`, `app_rankings`, `quest_tasks`, `quest_status`, `youtube_subscriptions`)
も `migrations/` に無いため含まれない。
