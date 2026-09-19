import atexit
import logging
import threading
import time
import traceback
import os
from logging.handlers import WatchedFileHandler
import config
from core import discord as core_discord

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

# Issue #759 (AUDIT-030): 同一内容のエラーを再通知するまでの最短間隔(秒)。
# DISCORD_MAX_INFLIGHT_SENDERS は「同時実行数」の制限であって「レート」の制限では
# なく、各送信が高速に完了すれば1分間に数百通送れてしまう。scheduler のタスク失敗
# (5分毎 → 1日288通)・camera_monitor の接続失敗(30秒毎 → 1日2,880通)のような
# 繰り返しエラーで通知が洪水になり、同時期の別の(より重要な)エラーが見落とされる。
# memory_monitor(check_cooldown)や server_watchdog(ロックファイルの mtime)が
# 個別に持っているクールダウンの発想を、logger.error → Discord の汎用経路にも
# 一般化する。10分にしてあるので「1時間に1回の cron タスクが毎回失敗する」ケースは
# 抑制されずに毎回通知される。
DISCORD_DEDUP_WINDOW_SEC = 600.0
# 重複排除テーブルの上限。超えたら最も古いものから捨てる(無制限に増やさない)。
DISCORD_DEDUP_MAX_ENTRIES = 256

_inflight_senders: "set[threading.Thread]" = set()
_inflight_lock = threading.Lock()

# 重複排除の状態: content_key -> (直近の送信時刻, ウィンドウ内で抑制した件数)
_recent_notifications: "dict[str, tuple[float, int]]" = {}
_dedup_lock = threading.Lock()

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


def _dedup_key(record: logging.LogRecord) -> str:
    """重複排除のキー。「同じログ行から出たエラー」を1つとみなす。

    フォーマット後の文字列ではなく**フォーマット前の record.msg** を使うのは、
    f-string でない `logger.error("... %s", arg)` 形式で引数だけが違う場合でも
    同じ発生源として束ねたいため。f-string の場合は引数が埋め込まれた後の
    文字列が msg になるため、値が変わるたびに別のキーになる(=通知される)。
    """
    return f"{record.name}:{record.levelno}:{record.msg}"


def _claim_notification_slot(content_key: str, now: float) -> "tuple[bool, int]":
    """重複排除の判定。(送ってよいか, 直前のウィンドウで抑制した件数) を返す。

    Issue #759 (AUDIT-030)。抑制した件数を次回の通知に載せることで、
    「静かになった」のか「抑制されている」のかを運用側から区別できるようにする。
    """
    with _dedup_lock:
        # 判定は掃除より先に行う。順序を逆にすると、ウィンドウを過ぎたエントリが
        # 掃除で消えてしまい「溜まった抑制件数を次の通知で報告する」経路に
        # 一度も到達しない(抑制件数が常に0になる)。
        entry = _recent_notifications.get(content_key)
        if entry is not None:
            last_ts, suppressed = entry
            if now - last_ts <= DISCORD_DEDUP_WINDOW_SEC:
                _recent_notifications[content_key] = (last_ts, suppressed + 1)
                return False, 0
            # ウィンドウを過ぎているので送る。溜まった抑制件数を引き継いで報告する。
            _recent_notifications[content_key] = (now, 0)
            return True, suppressed

        # 報告すべき抑制件数を持たない古いエントリだけを掃除する
        # (件数を抱えたままのエントリは、再発時に報告できるよう残す)。
        for key in [k for k, (ts, n) in _recent_notifications.items()
                    if n == 0 and now - ts > DISCORD_DEDUP_WINDOW_SEC]:
            _recent_notifications.pop(key, None)
        # それでも上限に達しているなら最も古いものから捨てる(無制限に増やさない)。
        while len(_recent_notifications) >= DISCORD_DEDUP_MAX_ENTRIES:
            oldest = min(_recent_notifications, key=lambda k: _recent_notifications[k][0])
            _recent_notifications.pop(oldest, None)

        _recent_notifications[content_key] = (now, 0)
        return True, 0


def reset_discord_dedup_state() -> None:
    """重複排除の状態を消す(テスト用。本番経路からは呼ばない)。"""
    with _dedup_lock:
        _recent_notifications.clear()


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


# #661: 正規表現の実体は core/discord.py へ移した(ここは後方互換のための残置なし)。


def _redact_webhook_url(text: str) -> str:
    """Discord Webhook URL のトークン部分をマスクする(core.discord への委譲。#661)。"""
    return core_discord.redact_webhook_url(text)


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
        # Issue #742 (AUDIT-013): 以前は `"Discord" not in str(record.msg)` を条件に
        # していた。要件は「通知システム自身の失敗を通知しようとしない(無限ループ防止)」
        # だが、それを**メッセージ内容の推測**で実装していたため対象が広すぎ、
        # smart_timelapse_generator の「Discord Webhook URLが設定されていないため動画を
        # 送信できません」「Discord送信失敗: {file_name}」のような**実障害まで無音**に
        # なっていた(タイムラプス配信の失敗が誰にも通知されない)。さらに record.msg は
        # フォーマット**前**の文字列のため、`logger.error("... %s", arg)` 形式では
        # 引数側の「Discord」が判定されず、抑制の挙動がログの書き方に依存していた。
        # 明示的なフラグ(extra={"skip_discord": True})による opt-out に変える。
        # 無限ループの安全性は維持される: 抑制するのは notification_service が
        # Discord 送信に失敗したときだけで、加えて _send_webhook 自身の失敗は
        # _webhook_failure_logger(propagate=False の独立ロガー)へ逃がしてあるため、
        # ハンドラ内部からの再入は構造的に起きない。
        if record.levelno >= logging.ERROR and not getattr(record, "skip_discord", False):
            try:
                # ★修正: 指定されたURLがあれば使い、なければデフォルト設定を使う
                url = self.webhook_url or config.DISCORD_WEBHOOK_ERROR
                if not url:
                    return

                # Issue #759 (AUDIT-030): 同一内容の繰り返しをウィンドウ内で束ねる。
                # URL 解決の後に判定するのは、Webhook 未設定で送らなかった分を
                # 「送信済み」として記録してしまわないため。
                should_send, suppressed_count = _claim_notification_slot(
                    _dedup_key(record), time.monotonic()
                )
                if not should_send:
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

                # 抑制していた件数を添える。「静かになった」のか「抑制されている」のかを
                # 運用側から区別できるようにするため(Issue #759)。
                if suppressed_count > 0:
                    window_min = int(DISCORD_DEDUP_WINDOW_SEC // 60)
                    content += (
                        f"\n（同じエラーが直近{window_min}分で {suppressed_count} 件抑制されました）"
                    )

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
            # #661: 送信(と 429/5xx の限定リトライ)は core/discord.py に一本化した。
            # core.discord は core.logger を import しないため循環にはならない。
            core_discord.post_with_retry(url, json=payload, timeout=5)
        except Exception as e:
            # #436: 以前はここで完全に握りつぶしており、Webhook URL失効やネットワーク障害で
            # 通知システム自体が壊れていても誰も気づけなかった。最低限の可視化として
            # 標準エラー出力に警告ログを残す。
            # URL にはWebhookトークンが含まれるため、ID部分だけ残してマスクして出力する。
            # exc_info(トレースバック)は requests の例外メッセージ経由で生URLを含むため付けず、
            # 例外種別とマスク済みメッセージのみを残す。
            _webhook_failure_logger.warning(
                "Discord webhook送信に失敗しました: %s (%s: %s)",
                _redact_webhook_url(url), type(e).__name__, _redact_webhook_url(e),
            )

def setup_logging(name: str, webhook_url: str = None) -> logging.Logger:
    """ロガーのセットアップ"""
    logger = logging.getLogger(name)
    logger.propagate = False
    
    # 同名ロガーの再セットアップ時は、既存ハンドラを close() してから外す。
    # 以前は handlers.clear() だけだったため、WatchedFileHandler が開いていた
    # home_system.log のファイルディスクリプタが閉じられずに残り、関数内で
    # get_logger() を呼ぶ経路(DDD/newface_monitor.py の storage_warmup 等)では
    # 呼び出しのたびに fd がリークしていた(pytest の ResourceWarning でも検出)。
    for existing_handler in list(logger.handlers):
        logger.removeHandler(existing_handler)
        try:
            existing_handler.close()
        except Exception:
            pass
    
    # Issue #665: レベルは config.LOG_LEVEL(環境変数 LOG_LEVEL、既定 INFO)。不正値は INFO。
    level_name = str(getattr(config, "LOG_LEVEL", "INFO") or "INFO").upper()
    logger.setLevel(getattr(logging, level_name, None) if isinstance(getattr(logging, level_name, None), int) else logging.INFO)
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
