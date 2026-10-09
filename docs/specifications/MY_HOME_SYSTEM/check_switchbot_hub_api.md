## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `tools/check_switchbot_hub_api.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |
| 解析基準コミット | Issue #881 での新規追加時点 |

## 関連ドキュメント

* [switchbot_service.md](./switchbot_service.md) - 認証ヘッダー生成 `create_switchbot_auth_headers` の提供元
* [config.md](./config.md) - `SWITCHBOT_API_HOST` / `SWITCHBOT_API_TOKEN` / `SWITCHBOT_API_SECRET` の提供元

## 2. ファイルの概要

SwitchBot ハブの稼働時と停止時で、SwitchBot API の応答に差が出るかを実機で確かめるための、**読み取り専用**の確認用 CLI。常駐処理・cron からは呼ばれず、人が実機で手動実行する。

**Issue #881**: 高砂のハブはラズパイと別LANのため ping 死活監視（`monitors/switchbot_hub_monitor.py`）が使えない。クラウド側の断を API 応答で検知できるかは「ハブ停止時に API が何を返すか」が未確認のため決められない。そこで稼働中・停止中のスナップショットを保存し、`--diff` で差が出た項目だけを見て判断する。差が出なければ高砂は対象外のままにする。

* `--save FILE`: スナップショット（`GET /v1.1/devices` のうち `deviceType` に `hub` を含む機器と、各ハブの `GET /v1.1/devices/<id>/status` の HTTP ステータス・本文）を JSON で標準出力し、指定時はファイルにも保存する。
* `--diff BEFORE AFTER`: 保存済みの2ファイルを比較し、差が出た項目を `項目: 前 -> 後` で表示する（API は呼ばない。`captured_at` は比較から除く）。
* 認証情報は出力に含めない。API 呼び出しは1回の実行で「1 + ハブ台数」回。
* 終了コード: `0` 正常 / `1` トークン・シークレット未設定 / `2` ハブが一覧に見つからない。

## 3. 外部依存関係

| 名称 | 種類 | 用途 |
| --- | --- | --- |
| `requests` | サードパーティ | API の GET（タイムアウト `REQUEST_TIMEOUT_SEC` = 10秒）。通信失敗は例外にせず `error` として記録する |
| `config` | 自作 | API ホスト・トークン・シークレット |
| `services.switchbot_service` | 自作 | `create_switchbot_auth_headers()` で署名付きヘッダーを作る |
| `argparse` / `json` / `os` / `sys` / `datetime` / `typing` | 標準 | 引数解析・保存・`sys.path` への親ディレクトリ追加・撮影時刻・型注釈 |

## 4. 関数

| 関数 | 役割 |
| --- | --- |
| `_get(path)` | API を GET し、HTTP ステータスと JSON 本文（JSON でなければ先頭500文字）を返す。通信例外は `{"http_status": None, "error": ...}` |
| `collect()` | 機器一覧と各ハブの status を集めたスナップショットを返す |
| `_flatten(value, prefix)` | 入れ子の dict/list を `a.b[0].c` 形式のキーへ平坦化する |
| `diff(before, after)` | 2つのスナップショットの差を文字列のリストで返す |
| `main(argv)` | 引数に応じて `--diff` / 収集・保存を実行する |

## 5. テスト

`tests/test_check_switchbot_hub_api.py`（API はモック）: ハブのみの収集・認証情報が出力に出ないこと・通信失敗の記録・差分の抽出（撮影時刻は無視）・`--diff` が API を呼ばないこと・資格情報未設定時の終了コード。
