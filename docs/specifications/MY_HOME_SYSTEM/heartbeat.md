## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | core/heartbeat.py |
| 解析基準コミット | Issue #1042 での新規作成時点(2026-10-25) |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

* [state_file.md](./state_file.md) - 日付の保存に使う `read_text` / `write_text_atomic` の実装元
* [nas_monitor.md](./nas_monitor.md)・[nature_remo_monitor.md](./nature_remo_monitor.md)・[server_watchdog.md](./server_watchdog.md) - 呼び出し元

## 2. ファイルの概要

正常時に無音で動く監視スクリプトの生存確認用に、**1日1回だけ** INFO で `💓 [heartbeat] <名前> は稼働中です` を出す共通ヘルパー。Issue #1042 で、ログ調査の時点では nas_monitor・server_watchdog・nature_remo_monitor が「動いていない」のか「正常で無音」なのか判断できなかったため追加した。

実行のたびに出すと nature_remo_monitor(5分間隔)でログが埋まるので、JST の日付を `config.BASE_DIR/data/heartbeat/<名前>.txt` に1行で保存し、同じ日の2回目以降は出さない。状態は監視ごとに別ファイルにする(共有ファイルだと後勝ちで他の監視の記録を消すため)。状態ファイルの書き込みに失敗しても例外は出さず、その場合は実行のたびに出るだけでログが増える以外の害は無い。

## 3. 外部依存関係

| 名称 | 種類 | 用途 |
| --- | --- | --- |
| `config` | 内部 | `BASE_DIR`(状態ファイルの置き場所) |
| `core.state_file` | 内部 | `read_text` / `write_text_atomic` |
| `core.utils.get_today_date_str` | 内部 | JST の今日の日付 |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `log_daily_heartbeat(name, logger, detail=None) -> bool`

* 今日まだ出していなければ INFO を1行出して状態ファイルへ今日の日付を書き、`True` を返す。出済みなら何もせず `False`
* `detail` を渡すと末尾に `(detail)` を付ける
* `name` は状態ファイル名に使うため、英数字・`_`・`.`・`-` 以外は `_` に置換する(パス区切りによるディレクトリ脱出を防ぐ)

## 8. 保守上の注意点

* heartbeat は「そのスクリプトが最後まで走った」ことだけを示し、中身が正しく動いたことまでは保証しない。
* ログローテーションで過去のログが消えると、その日の heartbeat も消える。「今日のログに heartbeat があるか」で見ること。
* 状態ファイルは `data/`(gitignore 済み)配下に置く。
