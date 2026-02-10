import logging
import traceback
import os
import requests
from logging.handlers import TimedRotatingFileHandler
import config

# === ロギング設定 ===
class DiscordErrorHandler(logging.Handler):
    """エラーログをDiscordに通知するハンドラ (スタックトレース対応版)"""
    # ★追加: 初期化時にWebhook URLを受け取れるようにする
    def __init__(self, webhook_url=None):
        super().__init__()
        self.webhook_url = webhook_url
    
    
    def emit(self, record):
        if record.levelno >= logging.ERROR and "Discord" not in record.msg:
            try:

                # ★修正: 指定されたURLがあれば使い、なければデフォルト設定を使う
                url = self.webhook_url or config.DISCORD_WEBHOOK_ERROR
                if not url:
                    return
                
                
                log_msg = self.format(record)
                
                stack_trace = ""
                if record.exc_info:
                    stack_trace = "".join(traceback.format_exception(*record.exc_info))
                elif record.levelno >= logging.ERROR:
                    stack_trace = "".join(traceback.format_stack())
                
                content = f"😰 **システムエラー発生**\n```python\n{log_msg}\n```"
                
                if stack_trace:
                    trace_snippet = stack_trace[-1000:]
                    content += f"\n**Stack Trace (End):**\n```python\n{trace_snippet}```"

                payload = {"content": content}
                requests.post(url, json=payload, timeout=5)
            except Exception:
                pass

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
    log_dir = os.path.join(config.BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "home_system.log")
    file_handler = TimedRotatingFileHandler(
        filename=log_file,
        when='midnight',
        interval=1,
        backupCount=7,
        encoding='utf-8'
    )
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