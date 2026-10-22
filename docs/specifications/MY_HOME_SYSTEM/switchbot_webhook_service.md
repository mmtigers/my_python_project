## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `services/switchbot_webhook_service.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

- [webhook_router.md](./webhook_router.md) — 本モジュールの `verify_token` / `extract_event` を呼び出す `/webhook/switchbot` エンドポイント(Issue #954 で判定ロジックを本モジュールへ移した)
- [config.md](./config.md) — `SWITCHBOT_WEBHOOK_TOKEN`・`ALLOW_UNAUTHENTICATED_SWITCHBOT_WEBHOOK` の定義元
- [logger.md](./logger.md) — `setup_logging` の実装元(ERROR は Discord へ自動通知される)

## 2. ファイルの概要

* SwitchBot Webhook の受信前処理のうち、I/O を伴わない判定(共有シークレット検証・イベントの抽出)を担うサービス。
* DB保存・SwitchBot API 呼び出し・センサーロジックの呼び出しはルーター側に残る。
* 根拠: [モジュールdocstring] (行番号: 1-9 / 抜粋: "SwitchBot Webhook の受信前処理")

## 3. 外部依存関係

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `hmac` | 標準ライブラリ | トークンの定数時間比較 | `import hmac` (行番号: 11 / 抜粋: "import hmac") |
| `HTTPException` | `fastapi` | 検証失敗時の 503/401 送出 | `from fastapi import HTTPException` (行番号: 14 / 抜粋: "from fastapi import HTTPException") |
| `config` | ローカルモジュール | トークン設定の参照 | `import config` (行番号: 16 / 抜粋: "import config") |
| `SwitchBotWebhookBody` | `models.switchbot` | ペイロードの型 | `from models.switchbot import SwitchBotWebhookBody` (行番号: 18 / 抜粋: "from models.switchbot import SwitchBotWebhookBody") |

## 4. 主要要素の定義

### `TARGET_DEVICE_TYPES`

* 対象センサーのデバイスタイプ集合。デバイス一覧APIの語彙(`Contact Sensor`/`Motion Sensor`)と公式Webhookの語彙(`WoContact`/`WoPresence`)の両方を許容する。
* 根拠: `TARGET_DEVICE_TYPES = {` (行番号: 27 / 抜粋: "TARGET_DEVICE_TYPES = {")

### `WebhookEvent`

* 受け付けたイベント(`mac`・`device_type`・小文字化済み `state`)の `NamedTuple`。
* 根拠: `class WebhookEvent(NamedTuple):` (行番号: 36 / 抜粋: "class WebhookEvent(NamedTuple):")

### `verify_token`

* `?token=` の共有シークレットを検証する。トークン未設定は 503(フェイルクローズ。`ALLOW_UNAUTHENTICATED_SWITCHBOT_WEBHOOK=true` のときだけ素通り)、不一致・欠落は 401。
* 未設定拒否の ERROR ログはプロセスごとに最初の1回だけ(以降は debug)。外部から通知を大量発火させられないため(Issue #648)。
* 比較は bytes に揃えた `hmac.compare_digest`。str のままだと非ASCIIで `TypeError` となり 500 になる。
* 根拠: `def verify_token(token: Optional[str]) -> None:` (行番号: 44 / 抜粋: "def verify_token(token: Optional[str]) -> None:")

### `extract_event`

* `ctx.deviceType` を優先し、無ければトップレベルの `deviceType`、それも無ければ `"Unknown"` とする。対象外タイプは `None`(debugログのみ)。
* `WoContact`/`Contact Sensor` で `ctx.openState` があれば開閉状態としてそれを使い(Issue #251)、それ以外は `detectionState` を使う。いずれも小文字化する。
* 根拠: `def extract_event(body: SwitchBotWebhookBody) -> Optional[WebhookEvent]:` (行番号: 85 / 抜粋: "def extract_event(body: SwitchBotWebhookBody) -> Optional[WebhookEvent]:")

## 8. 保守上の注意点

* このエンドポイントは Cloudflare Access をバイパスし `ip_restriction_middleware` の対象外のため、トークンが唯一の防御になる(Issue #321/#517/#648)。フェイルクローズを緩めないこと。
* 挙動は `tests/` の webhook 関連テストが固定している。
