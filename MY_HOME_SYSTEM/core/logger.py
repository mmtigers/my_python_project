import atexit
import logging
import threading
import time
import traceback
import os
import requests
from logging.handlers import WatchedFileHandler
import config

# Discord の content 上限は 2000 文字。コードフェンス等の装飾分の余裕を見て
# 1900 文字で切り詰める(#361: 以前は無制限に連結しており、長いエラーほど 400 で
# 無言で消えていた)。
DISCORD_CONTENT_LIMIT = 1900
# 同時に生存できる送信スレッド数の上限。ループ内でERRORが連発した場合に
# スレッドが積み上がるのを防ぐ(超過分は破棄する)。
DISCORD_MAX_INFLIGHT_SENDERS = 16
# プロセス終了時に送信中スレッドを待つ最大秒数(#361/D-M2: cron の短命プロセスでは
# 終了間際の ERROR がデーモンスレッドごと殺されて届かなかった)。
DISCORD_ATEXIT_FLUSH_SECONDS = 5.0

_inflight_senders: "set[threading.Thread]" = set()
_inflight_lock = threading.Lock()

# #436: _send_webhook の失敗を最低限どこかに残すための独立ロガー。
# アプリの名前付きロガー(setup_logging()でハンドラをclearされうる)とは
# 別名にして、通知システム自体の障害を検知できるようにする。
_webhook_failure_logger = logging.getLogger("core.logger.discord_webhook_failure")
if not _webhook_failure_logger.handlers:
    _webhook_failure_handler = logging.StreamHandler()
    _webhook_failure_handler.setFormatter(
        logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    )
    _webhook_failure_logger.addHandler(_webhook_failure_handler)
    _webhook_failure_logger.propagate = False


def _register_sender(thread: threading.Thread) -> None:
    with _inflight_lock:
        # 終了済みスレッドを掃除してから登録する
        for t in [t for t in _inflight_senders if not t.is_alive()]:
            _inflight_senders.discard(t)
        _inflight_senders.add(thread)


def _inflight_count() -> int:
    with _inflight_lock:
        return sum(1 for t in _inflight_senders if t.is_alive())


def flush_pending_discord_notifications(timeout: float = DISCORD_ATEXIT_FLUSH_SECONDS) -> None:
    """送信中の Discord 通知スレッドを timeout 秒まで待つ(atexit から呼ばれる)。"""
    deadline = time.monotonic() + timeout
    with _inflight_lock:
        threads = list(_inflight_senders)
    for t in threads:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        t.join(timeout=remaining)


atexit.register(flush_pending_discord_notifications)


def _truncate_discord_content(content: str, limit: int = DISCORD_CONTENT_LIMIT) -> str:
    if len(content) <= limit:
        return content
    marker = "\n…(切り詰め)"
    return content[: limit - len(marker)] + marker


# === ロギング設定 ===
class DiscordErrorHandler(logging.Handler):
    """エラーログをDiscordに通知するハンドラ (スタックトレース対応版)"""
    # ★追加: 初期化時にWebhook URLを受け取れるようにする
    def __init__(self, webhook_url=None):
        super().__init__()
        self.webhook_url = webhook_url
    
    
    def emit(self, record):
        # M-5-5(Low): record.msg は例外オブジェクト等の非文字列が渡される場合もあるため、
        # str化してから比較する("Discord" not in record.msg は非文字列だとTypeErrorになりうる)。
        if record.levelno >= logging.ERROR and "Discord" not in str(record.msg):
            try:
                # ★修正: 指定されたURLがあれば使い、なければデフォルト設定を使う
                url = self.webhook_url or config.DISCORD_WEBHOOK_ERROR
                if not url:
                    return


                log_msg = self.format(record)

                # #361: 以前は exc_info が無い ERROR でも format_stack() を常に付けていたため
                # (logger.error() の呼び出し元スタックで情報量は少ない)、本文が約900字を超えると
                # 2000字制限で 400 になっていた。スタックトレースは例外情報がある場合のみ付ける。
                stack_trace = ""
                if record.exc_info:
                    stack_trace = "".join(traceback.format_exception(*record.exc_info))

                # 本文自体が長すぎる場合(scheduler が流す子プロセスの stderr 全文など)は
                # 先頭側を残して切り詰める。
                body_limit = DISCORD_CONTENT_LIMIT - 200
                if len(log_msg) > body_limit:
                    log_msg = log_msg[:body_limit] + "\n…(切り詰め)"

                content = f"😰 **システムエラー発生**\n```python\n{log_msg}\n```"

                if stack_trace:
                    room = DISCORD_CONTENT_LIMIT - len(content) - 60
                    if room > 100:
                        trace_snippet = stack_trace[-min(1000, room):]
                        content += f"\n**Stack Trace (End):**\n```python\n{trace_snippet}```"

                payload = {"content": _truncate_discord_content(content)}
                # M-5-5: emit()はログ出力のたびにリクエスト処理スレッド上で呼ばれるため、
                # ここで同期的にrequests.postすると、Discord側が遅い/落ちている場合に
                # そのスレッドをtimeout秒(最大5秒)ブロックしてしまう。バックグラウンド
                # スレッドで送信し、emit()自体は即座に返すようにする。
                # #361: 送信スレッドは上限付きで追跡し、プロセス終了時(atexit)に join する。
                if _inflight_count() >= DISCORD_MAX_INFLIGHT_SENDERS:
                    return
                sender = threading.Thread(
                    target=self._send_webhook, args=(url, payload), daemon=True
                )
                _register_sender(sender)
                sender.start()
            except Exception:
                # logging.Handler標準のhandleError()を使う。sys.stderrへ直接書き出すのみで
                # 再度loggingを経由しないため、ここで失敗を握りつぶしても無限ループにはならない。
                self.handleError(record)

    @staticmethod
    def _send_webhook(url, payload):
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception:
            # #436: 以前はここで完全に握りつぶしており、Webhook URL失効やネットワーク障害で
            # 通知システム自体が壊れていても誰も気づけなかった。最低限の可視化として
            # 標準エラー出力に警告ログを残す。
            _webhook_failure_logger.warning("Discord webhook送信に失敗しました: %s", url, exc_info=True)

def setup_logging(name: str, webhook_url: str = None) -> logging.Logger:
    """ロガーのセットアップ"""
    logger = logging.getLogger(name)
    logger.propagate = False
    
    if logger.handlers:
        logger.handlers.clear()
    
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    # コンソール出力
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # ファイル出力
    # #384: 以前は BASE_DIR/logs 固定だったため、config.LOG_DIR が書き込み失敗で
    # temp_fallback/logs に落ちた場合に、health_watch/log_analyzer が読む場所と
    # 実際のログ出力先が食い違っていた。config.LOG_DIR(フォールバック解決済み)に一本化する。
    log_dir = getattr(config, "LOG_DIR", None) or os.path.join(config.BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "home_system.log")
    # home_system.log は unified_server / monitors / cronスクリプト等の複数プロセスが
    # 同時に開くため、各プロセスが独自にrenameするTimedRotatingFileHandlerでは
    # ローテーションが壊れる(旧backupへ書き込み続ける)。書き込み専用の
    # WatchedFileHandlerにし、ローテーションはlogrotate側
    # (deploy/logrotate/home_system → /etc/logrotate.d/home_system)に一元化する。
    file_handler = WatchedFileHandler(filename=log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Discord通知
    # ★追加: 引数でURLが指定されていれば優先、なければconfig.DISCORD_WEBHOOK_ERRORを使用
    target_url = webhook_url or getattr(config, "DISCORD_WEBHOOK_ERROR", None)

    if target_url:
        discord_handler = DiscordErrorHandler(webhook_url=target_url)
        discord_handler.setLevel(logging.ERROR)
        discord_handler.setFormatter(formatter)
        logger.addHandler(discord_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """setup_logging() のエイリアス。`from core.logger import get_logger` で参照される呼び出し元向け。"""
    return setup_logging(name)
