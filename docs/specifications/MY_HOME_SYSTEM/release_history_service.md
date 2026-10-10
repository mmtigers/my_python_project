## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `release_history_service.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

* [dashboard_router.md](./dashboard_router.md) - `GET /dashboard/updates`が`get_release_history`を呼ぶ
* [dashboard_page_service.md](./dashboard_page_service.md) - `render_updates_page`(表示側。**外部由来の文字列なのでエスケープして出す**)
* [config.md](./config.md) - `ASA_NOTE_URL`/`YORU_NOTE_URL`(外部アプリの取得元)
* [../family-quest/src/lib/changelog.md](../family-quest/src/lib/changelog.md) - ファミクエ側の履歴データ(`changelog.json`)

## 2. ファイルの概要

お家ダッシュボードの「アップデート」ページ用に、全アプリの更新履歴を集めて1本にまとめるサービス。**各アプリが自分の履歴を持ち、ここは読んで結合するだけ**(二重管理しない)。

| アプリ | 取得元 | 種別 |
| --- | --- | --- |
| ダッシュボード | `MY_HOME_SYSTEM/dashboard_releases.json`(手書き。新しい順) | ローカルファイル |
| ファミクエ | `family-quest/src/lib/changelog.json`(アプリ内「アップデートのれきし」と同じファイル) | ローカルファイル |
| あさノート | `<ASA_NOTE_URL>releases.json`(アプリが公開) | HTTP |
| よるノート | `<YORU_NOTE_URL>releases.json`(アプリが公開) | HTTP |

* 外部(HTTP)の取得は、タイムアウト`REMOTE_TIMEOUT_SEC`(3秒)・サイズ上限`MAX_RESPONSE_BYTES`(1MB)・件数上限`MAX_ENTRIES_PER_SOURCE`(50)・1件の詳細数上限`MAX_DETAILS_PER_ENTRY`(20)・文字数上限`MAX_TEXT_LEN`(500)を付け、成功は`CACHE_TTL_SEC`(600秒)キャッシュする。失敗は`NEGATIVE_CACHE_TTL_SEC`(60秒)だけ覚えて、閲覧のたびに外部へ問い合わせて待たされないようにする。失敗したとき、直前に取れた内容があれば「古い内容」(`stale`)として返し、無ければ空にする。
* 外部の2アプリは`ThreadPoolExecutor`で並列に取得するので、待ちは最大でもタイムアウト程度。
* 取得した内容は外部由来のデータ。HTML/JSONでなくても、本文は文字列として扱い、画面側で必ずエスケープする。
* どの公開関数も例外を送出しない(ページ全体を落とさない)。
* 根拠: [定数] (行番号: 42 / 抜粋: "APPS: tuple[tuple[str, str], ...] = ("), [関数定義] (行番号: 188 / 抜粋: "def get_release_history(")

## 3. 外部依存関係

| 名称 | 種類 | 用途 |
| --- | --- | --- |
| `requests` | 外部ライブラリ | あさノート・よるノートの`releases.json`の取得(`stream=True`でサイズ上限を守る) |
| `config.BASE_DIR`/`config.ASA_NOTE_URL`/`config.YORU_NOTE_URL` | 内部 | ローカルファイルの場所と外部アプリのURL |
| `concurrent.futures.ThreadPoolExecutor` | 標準 | 4アプリの取得を並列に行う |

## 4. 関数

### `normalize_entries`

* **役割**: アプリごとの形式のまま読み込んだ履歴を、共通の形`{app, app_label, version, date, title, summary, details[]}`にそろえる。ファミクエは`changes`を`details`として扱う。`date`が`YYYY-MM-DD`でない、タイトル・要約・詳細がすべて空、辞書でない要素は捨てる。リストでなければ空。文字列は`MAX_TEXT_LEN`で、件数は上限で切り詰める。文字列でない値は空として扱い、例外にしない。
* 根拠: `def normalize_entries(raw: Any, app: str) -> list[dict[str, Any]]:` (行番号: 80)

### `get_release_history`

* **役割**: 全アプリの更新履歴を、日付の新しい順(同じ日付は`APPS`の順、同じアプリ・同じ日付はファイル内の順)にまとめて返す。
* 根拠: `def get_release_history() -> dict[str, Any]:` (行番号: 188)
* **戻り値**: `{"entries": [...], "sources": [{app, label, entries, ok, stale, error}, ...]}`。取得に失敗したアプリは`ok: false`・`error`入りで、他のアプリの表示には影響しない。

### `clear_cache`

* **役割**: キャッシュ(成功・失敗・直前に成功した内容)を空にする。テスト用。
* 根拠: `def clear_cache() -> None:` (行番号: 203)
