import requests
import logging
import tenacity
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def get_retry_session(retries=3, backoff_factor=1.0):
    """リトライ機能付きのRequestsセッションを作成"""
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS", "TRACE"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def create_resilient_session(retries=3, backoff_factor=2, status_forcelist=(500, 502, 504)):
    """(旧API互換) 高耐久セッション作成"""
    session = requests.Session()
    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=["HEAD", "GET", "POST", "OPTIONS"],
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

def retry_api_call(func):
    """API呼び出し用リトライデコレータ"""
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        # 循環参照を避けるため、logger取得は実行時に行うか、呼び出し元で制御する設計が望ましいが
        # ここではlogging.getLoggerで直接取得する
        before_sleep=tenacity.before_sleep_log(logging.getLogger("core.network"), logging.WARNING),
        reraise=True
    )(func)