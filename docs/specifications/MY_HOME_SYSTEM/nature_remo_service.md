## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `nature_remo_service.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

- [switchbot_service.md](./switchbot_service.md) — 呼び出し元(`turn_off_tv_gracefully`)
- [config.md](./config.md) — `TV_NATURE_REMO_LOCATION`・`TV_REMO_SIGNAL_ID`・`TV_REMO_APPLIANCE_ID`・アクセストークンの提供元
- [logger.md](./logger.md) — `setup_logging`の実体

## 2. ファイルの概要

* Nature Remo（クラウドAPI）でテレビの電源信号を送るサービスです。プラグの100Vを断つ前に、テレビがついていればリモコンで先に消すために使います。
* 送り方は2通りで、`TV_REMO_SIGNAL_ID`（学習させた信号。`POST /1/signals/{id}/send`）があればそれを、なければ`TV_REMO_APPLIANCE_ID`（種類が「TV」の家電。`POST /1/appliances/{id}/tv`の`button=power`）を使います。
* 電源ボタンはトグルの場合が多いため、呼び出し側は消費電力でテレビがついていると確認できたときだけ送ります。

## 3. 主要な関数

### `is_tv_remote_configured`

* **役割**: 拠点のトークンと、信号IDまたは家電IDが揃っているかを返す。
* 根拠: `is_tv_remote_configured` (行番号: 36 / 抜粋: "def is_tv_remote_configured() -> bool:")

### `send_tv_power`

* **役割**: テレビの電源信号を送る。送信できたら`True`、失敗（未設定を含む）は`False`。例外は外へ出さない（呼び出し側は失敗してもプラグを切るため）。
* 根拠: `send_tv_power` (行番号: 41 / 抜粋: "def send_tv_power() -> bool:")
