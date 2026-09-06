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
参考ドキュメントであり、「あるべき姿」の記述として位置づける（本番DBの実機ダンプ
ではない）。上記のとおり**このディレクトリ(`migrations/`)がスキーマの唯一の
定義元**であり、`current_schema.sql` はそれと矛盾したり実行時の挙動を決定したり
しない。

2026-09時点で判明している既知の差分（Issue #411調査時点。解消の予定は無い）:

- `current_schema.sql` には `0000_baseline_schema.sql` が持つ `CREATE INDEX` 文が
  3つ含まれていない。
- `current_schema.sql` には baseline に存在しないテーブル(`haircut_history`,
  `app_rankings`, `quest_tasks`, `quest_status`, `youtube_subscriptions`)や列
  (`device_records.battery_level`、`food_records.date`/`menu`/`created_at`)が
  含まれている。特に `app_rankings` は `services/analysis_service.py` が参照する
  ため、空DB(`migrations/`のみ適用した状態)ではこの機能が黙って無効になる点に
  注意すること。

`tests/test_current_schema_sql.py` が、`migrations/*.sql` の
`ALTER TABLE ... ADD COLUMN` で追加される列が `current_schema.sql` の
`CREATE TABLE` 文に含まれているか(一方向のみ)を機械的にチェックしているが、
上記のような「`current_schema.sql` にしか無い」差分は検知しない。実際のDB
スキーマを確認する必要がある場合は、必ず本ディレクトリ(`migrations/`)を正とし、
`current_schema.sql` を参照する場合もこの位置づけを踏まえること。
