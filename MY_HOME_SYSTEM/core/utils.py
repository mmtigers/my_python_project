import datetime
import pytz
import time
import functools
import logging
import os
from pathlib import Path
from typing import Callable, Any, Union

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


def wait_for_storage_warmup(
    target_path: Union[str, Path], 
    max_retries: int = 5, 
    base_delay: float = 1.0, 
    max_delay: float = 16.0
) -> bool:
    """
    ストレージ（NAS等）へのアクセスが可能になるまで、指数関数的バックオフを用いて待機する。
    HDDのスピンダウン（スリープ）からの復帰遅延による Errno 2 エラーを防止するためのウォームアップ処理。

    Args:
        target_path (Union[str, Path]): アクセスを確認する対象のパス（ファイルまたはディレクトリ）。
        max_retries (int): 最大リトライ回数。デフォルトは5回。
        base_delay (float): 初回の待機時間（秒）。デフォルトは1.0秒。
        max_delay (float): 最大の待機時間（秒）。デフォルトは16.0秒。

    Returns:
        bool: 指定回数内にアクセス可能となった場合は True、不可の場合は False。
    """
    path_obj = Path(target_path)
    # ファイルパスが指定された場合は、その親ディレクトリが存在/アクセス可能かをチェック対象とする
    check_target = path_obj if path_obj.is_dir() else path_obj.parent

    for attempt in range(max_retries + 1):
        # パスの存在とアクセス権限（読み書き）をチェック
        if check_target.exists() and os.access(check_target, os.R_OK | os.W_OK):
            if attempt > 0:
                logger.info(f"💡 [Storage Warmup] ストレージが応答しました（{attempt}回のリトライで復帰）: {check_target}")
            return True
            
        if attempt < max_retries:
            # Exponential Backoff の計算
            delay = min(max_delay, base_delay * (2 ** attempt))
            logger.debug(
                f"⏳ [Storage Warmup] アクセス待機中（{attempt + 1}/{max_retries}回目）。"
                f"{delay}秒待機... 対象: {check_target}"
            )
            time.sleep(delay)
        
    logger.error(f"❌ [Storage Warmup] {max_retries}回リトライしましたが、ストレージにアクセスできません: {check_target}")
    return False