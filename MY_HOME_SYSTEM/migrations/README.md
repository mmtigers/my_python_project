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
