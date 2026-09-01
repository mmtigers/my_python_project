# develop

家庭環境の自動化・ロギング、ゲーミフィケーションを通じたタスク管理、外部データの自動収集と
コンテンツ生成を統合管理する複合型プラットフォーム。Raspberry Pi上で常駐稼働する。
3つの独立したサブシステムで構成され、互いに連携しながら自律的・非同期に稼働している。
全体アーキテクチャは[docs/specifications/全体設計書.md](docs/specifications/全体設計書.md)を参照。

## 構成

| ディレクトリ | 役割 |
| --- | --- |
| [MY_HOME_SYSTEM/](./MY_HOME_SYSTEM/README.md) | IoT機器の制御、環境データの収集・分析、各種API・Webhookの統合ルーティングを担うFastAPIバックエンド。 |
| [family-quest/](./family-quest/README.md) | クエスト管理をRPG風UIで提供するReact/TypeScriptフロントエンド。`MY_HOME_SYSTEM`が`/quest/`で配信する。 |
| [DDD/](./DDD/README.md) | 動画・画像等のデータ自動収集バッチ処理群。 |
| [docs/specifications/](./docs/specifications/README.md) | 各ソースファイルをリバースエンジニアリング解析した仕様書群。 |
| [deploy/](./deploy) | 実機(Raspberry Pi)のcrontab・systemdユニット等、コード化されない構成のバージョン管理。 |

## 実機構成の復元

実機の`crontab`・systemdユニットはこのリポジトリで管理している。復旧手順は
[deploy/cron/README.md](./deploy/cron/README.md)・
[MY_HOME_SYSTEM/deploy/systemd/README.md](./MY_HOME_SYSTEM/deploy/systemd/README.md)を参照。

## CI

`.github/workflows/`で、テスト(`test.yml`)と仕様書ドリフト監査(`spec-drift-*.yml`、
ソースと`docs/specifications/`の対応関係チェック)を実行している。
