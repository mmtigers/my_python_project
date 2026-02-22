import datetime
import pytz
import time
import functools
import logging
from typing import Callable, Any

logger = logging.getLogger("core")

def get_now_iso() -> str:
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).isoformat()

def get_today_date_str() -> str:
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%Y-%m-%d")

def get_display_date() -> str:
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%m/%d")

def with_exponential_backoff(
    base_delay: int = 5, 
    max_delay: int = 300, 
    alert_threshold: int = 5
) -> Callable:
    """
    関数実行時の例外を捕捉し、指数関数的バックオフを用いて無限リトライを行うデコレータ。
    Fail-Softを徹底し、一時的なネットワーク障害やデバイス再起動による恒久的なプロセス停止を防ぐ。
    
    Args:
        base_delay (int): 初回のリトライ待機時間（秒）。
        max_delay (int): 最大待機時間の上限（秒）。デフォルトは5分(300秒)。
        alert_threshold (int): エラーレベルをERRORに引き上げ、アラートの基準とする連続失敗回数。
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            attempt: int = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    attempt += 1
                    delay: int = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    
                    if attempt >= alert_threshold:
                        logger.error(f"❌ [{func.__name__}] 深刻なエラー継続中（{attempt}回目）: {e}")
                    else:
                        logger.warning(f"⚠️ [{func.__name__}] 実行エラー（{attempt}回目）。{delay}秒後にリトライ... 詳細: {e}")
                    
                    time.sleep(delay)
        return wrapper
    return decorator