# migrations/

スキーマ変更用のバージョン管理されたマイグレーションファイル置き場です。
`core/migrations.py` の `apply_pending_migrations()` が、このディレクトリ内の
`*.sql` をファイル名の昇順で読み込み、`schema_migrations` テーブルに記録された
適用済みバージョンと突き合わせて未適用分のみを実行します。

## 新しいマイグレーションの追加方法

1. `NNNN_short_description.sql` の形式でファイルを追加する（`NNNN` は既存の最大値+1のゼロ埋め4桁連番）。
2. 可能な限り `ALTER TABLE ... ADD COLUMN` を先頭に書き、後続のデータ移行(UPDATE等)はその後に続ける。
   - 既に列が存在する環境（過去の実行時チェックで先に適用済み等）に対して再実行された場合、
     `ALTER TABLE` の失敗（duplicate column）はランナー側で警告ログとして扱われ、
     処理は継続します。
3. 本番相当のダミーデータで一度動作確認してからコミットする。

## 実行タイミング

- `init_unified_db.init_db()`（テスト・初期セットアップ用）
- `unified_server.py` の起動時（`lifespan`）

のいずれからも呼び出されます。
