# DDD/conftest.py
"""
DDD配下のテスト共通フィクスチャ。

newface_monitor.py・extract_youtube_urls.py は起動時に MY_HOME_SYSTEM を
sys.path に追加し、本物の core.logger.get_logger()/config を import する
(単体テスト用フォールバックが無い環境が前提)。core.logger.setup_logging()は
import された時点(=各テストファイルの collection 時点)で
config.DISCORD_WEBHOOK_ERROR を DiscordErrorHandler に焼き込むため、以降
個々のテストの setUp/monkeypatch で config を書き換えても手遅れになる
(MY_HOME_SYSTEM/tests/conftest.py と同じ制約)。

DDDには元々pytest基盤(本ファイルを含むconftest.py)が存在せず、本番の
Raspberry Pi環境等、本物の認証情報が入った .env のある環境で
`pytest DDD/` を実行すると、ERRORログを出すテスト(破損ファイルの
読み込み失敗テスト等)がそのまま実Discord Webhookへの通知を発火させて
しまう経路が無防備なままだった(Issue #103)。MY_HOME_SYSTEM/tests/conftest.py
と同じ方式で、`import config` より前に環境変数そのものを空文字で潰しておく
(load_dotenv は既存の環境変数を上書きしないため有効)。
"""
import os

os.environ["DISCORD_WEBHOOK_ERROR"] = ""
os.environ["DISCORD_WEBHOOK_ERROR_CAM"] = ""
os.environ["DISCORD_WEBHOOK_REPORT"] = ""
os.environ["DISCORD_WEBHOOK_NOTIFY"] = ""
os.environ["DISCORD_WEBHOOK_URL"] = ""
os.environ["LINE_CHANNEL_ACCESS_TOKEN"] = ""
os.environ["LINE_USER_ID"] = ""
