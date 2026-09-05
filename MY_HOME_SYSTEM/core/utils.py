import contextlib
import datetime
import pytz
import threading
import time
import functools
import logging
import os
from pathlib import Path
from typing import Callable, Any, Dict, Optional, Tuple, Type, Union

logger = logging.getLogger("core")

def get_now_iso() -> str:
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).isoformat()

def get_today_date_str() -> str:
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%Y-%m-%d")

def get_display_date() -> str:
    return datetime.datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%m/%d")


class RefCountedLockRegistry:
    """キー単位の threading.Lock を参照カウント付きで管理するレジストリ。

    #435: quest_service.py の完了/残高/購入ロックは、キーの組み合わせ
    (ユーザーID×クエストID等)が増えるたびに threading.Lock エントリが
    無制限に蓄積していた。参照している呼び出しが居なくなった時点で
    エントリを辞書から削除することでこれを防ぐ。

    services/camera_service.py の _RefCountedLock/_vod_generation_lock と
    同じ考え方: ロック取得中(参照カウント>0)のエントリは絶対に削除しない。
    単純に「lock.locked()がFalseなら削除」する方式だと、辞書からロック
    オブジェクトを取り出した直後・実際にwith文で獲得する直前の隙間で
    別スレッドが剪定してしまい、同一キーに対して2つの別々のLockオブジェクトが
    生成されて同時に「取得成功」してしまう(排他制御が本来防ぐべき事態の再発)
    ため、参照カウントで安全性を担保する。
    """

    class _Entry:
        __slots__ = ("lock", "ref_count")

        def __init__(self) -> None:
            self.lock = threading.Lock()
            self.ref_count = 0

    def __init__(self) -> None:
        self._entries: Dict[Any, "RefCountedLockRegistry._Entry"] = {}
        self._guard = threading.Lock()

    @contextlib.contextmanager
    def acquire(self, key: Any):
        """key単位で排他制御するコンテキストマネージャ。"""
        with self._guard:
            entry = self._entries.get(key)
            if entry is None:
                entry = self._Entry()
                self._entries[key] = entry
            entry.ref_count += 1
        try:
            with entry.lock:
                yield
        finally:
            with self._guard:
                entry.ref_count -= 1
                if entry.ref_count == 0 and self._entries.get(key) is entry:
                    del self._entries[key]

    def keys(self):
        """現在エントリが存在するキー一覧(テスト・デバッグ用)。"""
        with self._guard:
            return list(self._entries.keys())

    def __contains__(self, key: Any) -> bool:
        with self._guard:
            return key in self._entries

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


def retry_with_backoff(
    fn: Callable[[], Any],
    *,
    max_retries: int,
    retryable_exceptions: Tuple[Type[BaseException], ...],
    base_delay: float = 1.0,
    max_delay: float = float("inf"),
    on_retry: Optional[Callable[[int, float, BaseException], None]] = None,
) -> Any:
    """
    NAS等マウント遅延の影響を受けるI/O処理向けの、Exponential Backoffによる
    リトライの共通ユーティリティ(Issue #292)。

    config.py の verify_and_initialize_storage と monitors/nas_monitor.py の
    check_write_permission が、リトライ回数・待機時間・対象例外の異なる
    Exponential Backoffループをそれぞれ個別に実装していたため、ループの
    メカニズム自体をここに集約する。リトライ対象の例外集合・回数・待機秒数
    という「ポリシー」自体は呼び出し元ごとに異なりうるため引数として残し、
    挙動そのものは変更しない(純粋なリファクタリング)。

    fn() を実行し、retryable_exceptions に該当する例外が発生した場合のみ、
    base_delay * 2^attempt 秒(max_delayで頭打ち)待機して最大 max_retries 回
    まで再試行する。全リトライを使い切った場合は最後に発生した例外を
    そのまま再送出する(呼び出し元でキャッチして最終的なフォールバック処理を
    行うこと)。

    Args:
        fn: 実行する引数なしのcallable。リトライごとに再度呼び出されるため、
            試行ごとに異なる状態(一意なファイル名等)が必要な場合はfn内で
            都度生成すること。
        max_retries: 初回実行を含まない追加リトライの最大回数。
        retryable_exceptions: リトライ対象とする例外クラスのタプル。
            これ以外の例外は即座に呼び出し元へ伝播する。
        base_delay: 初回リトライの待機秒数。
        max_delay: 待機秒数の上限(未指定の場合は上限なし)。
        on_retry: 各リトライ前に呼ばれるコールバック(attempt, delay, exception)。
            呼び出し元固有のログ出力に使う。

    Returns:
        fn() の戻り値。

    Raises:
        retryable_exceptions に該当する例外を、全リトライ失敗後に再送出する。
    """
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except retryable_exceptions as e:
            if attempt >= max_retries:
                raise
            delay = min(max_delay, base_delay * (2 ** attempt))
            if on_retry:
                on_retry(attempt, delay, e)
            time.sleep(delay)


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