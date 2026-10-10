## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `system_info_service.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

* [system_maintenance_service.md](./system_maintenance_service.md) - `_git`・再起動の状態ファイル(`RESTART_MARKER_FILE`・`DEPLOY_STATE_FILE`)の提供元
* [unified_server.md](./unified_server.md) - `lifespan`が起動時に`record_boot`を呼ぶ
* [system_router.md](./system_router.md) - `GET /api/system/git/status`が`get_git_status`を返す
* [dashboard_page_service.md](./dashboard_page_service.md) - システムページの「起動履歴」「Gitの状態」の表示側
* [migrations.md](./migrations.md) - `0023_add_server_boot_events.sql`(テーブル定義)

## 2. ファイルの概要

システムページの「サーバーの起動履歴」と「Gitの状態」のためのサービス。

* **起動履歴**: `unified_server`の起動時に`record_boot`が`server_boot_events`へ1行追記する(起動時刻・git短縮ハッシュ・コミット件名・ブランチ・PID・きっかけ)。`get_boot_history`が新しい順に返す。異常終了(電源断等)は記録できないため「起動した時刻」の履歴であり、「停止した時刻」の履歴ではない。きっかけ(`reason`)は`_determine_boot_reason`が、起動の直前にダッシュボードが残した記録から推定する(`update`=「最新に更新して再起動」の状態ファイルが`restarting`で、要求から`BOOT_REASON_WINDOW_SEC`(900秒)以内/`manual`=「システム再起動」の記録(`RESTART_MARKER_FILE`)が同じ窓以内/それ以外は`unknown`。`update`が`manual`より優先)。
* **Gitの状態**: 「起動中のコード(最後に記録した起動のコミット)」「手元(実機の作業ツリー)の最新」「GitHubの最新(追跡先ブランチ)」の3段を比べる(`get_git_status`)。GitHubの最新を知るため`git fetch`(タイムアウト`GIT_FETCH_TIMEOUT_SEC`=20秒)を実行する。外部通信で遅いため、ページ本体では呼ばず、ページ表示後にJSが`GET /api/system/git/status`から取得する。成功した結果は`GIT_STATUS_CACHE_TTL_SEC`(300秒)キャッシュし、失敗はキャッシュしない。
* どの公開関数も例外を送出しない(起動処理・ページ表示を落とさない)。失敗は警告ログにして、空/エラー入りの結果を返す。
* 根拠: [関数定義] (行番号: 100 / 抜粋: "def record_boot("), [関数定義] (行番号: 124 / 抜粋: "def get_boot_history("), [関数定義] (行番号: 210 / 抜粋: "def get_git_status(")

## 3. 外部依存関係

| 名称 | 種類 | 用途 |
| --- | --- | --- |
| `services.system_maintenance_service._git` | 内部(遅延import) | `git -C <リポジトリ> ...`の実行(認証入力待ちでハングさせない設定込み)。循環importと起動時負荷を避けるため関数内でimportする |
| `core.database.get_db_cursor` | 内部 | `server_boot_events`の読み書き |
| `core.state_file.read_json` | 内部 | 再起動要求の記録の読み取り |
| `core.utils.get_now_iso` | 内部 | 起動時刻(JSTオフセット付きISO8601) |

## 4. 定数

* `REASON_UPDATE`/`REASON_MANUAL`/`REASON_UNKNOWN`と`REASON_LABELS`(画面の表示名: 「ダッシュボードから更新」「ダッシュボードから再起動」「不明(自動復旧・電源など)」)。根拠: `REASON_UPDATE = "update"` (行番号: 30)
* `BOOT_REASON_WINDOW_SEC`=900.0、`BOOT_HISTORY_DISPLAY_LIMIT`=10、`GIT_STATUS_CACHE_TTL_SEC`=300.0、`GIT_FETCH_TIMEOUT_SEC`=20、`GIT_QUICK_TIMEOUT_SEC`=10、`RECENT_COMMITS_LIMIT`=10。

## 5. 関数

### `record_boot`

* **役割**: 起動した事実を`server_boot_events`へ追記する。`lifespan`でマイグレーション成功後に1回だけ呼ぶ。
* 根拠: `def record_boot() -> bool:` (行番号: 100)
* **戻り値**: 成功なら`True`。失敗(DB・git・その他)は警告ログにして`False`(サーバーの起動は止めない)。gitが使えないときはコミット・ブランチを`NULL`にして、起動自体は記録する。
* **副作用**: DBへの1行INSERT。`git rev-parse`/`git log`の実行。

### `get_boot_history`

* **役割**: 起動履歴を新しい順(`booted_at DESC, id DESC`)で返す。
* 根拠: `def get_boot_history(limit: int = BOOT_HISTORY_DISPLAY_LIMIT) -> list[dict[str, Any]]:` (行番号: 124)
* **戻り値**: 辞書のリスト(`booted_at`/`commit_sha`/`commit_subject`/`branch`/`pid`/`reason`)。テーブルが無い・読めないときは空リスト。

### `get_git_status`

* **役割**: 3段のGit状態と最近のコミットを返す。`ok`/`error`/`fetch_ok`/`branch`/`upstream`/`head`/`remote`/`running`/`behind`/`ahead`/`pending_restart`/`recent`を持つ。`pending_restart`は「起動中のコミット≠手元のHEAD」(更新済みだが未再起動)。`recent`は追跡先の先頭`RECENT_COMMITS_LIMIT`件を、手元へ取り込み済みか(`pulled`)・起動中のコミットか(`running`)の印付きで並べる。
* 根拠: `def get_git_status(force: bool = False) -> dict[str, Any]:` (行番号: 210)
* **エラーハンドリング**: リポジトリが無ければ`ok: false`。追跡先が無ければ`fetch`せず`remote: None`と注記。`git fetch`だけ失敗したときは手元の最後の追跡情報で比較し、`fetch_ok: false`と注記する。予期しない例外は`ok: false`の辞書にする。`force=True`でキャッシュを無視する。
