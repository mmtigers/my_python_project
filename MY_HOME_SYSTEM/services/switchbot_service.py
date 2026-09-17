# MY_HOME_SYSTEM/services/switchbot_service.py
import threading
import time
import hashlib
import hmac
import base64
import uuid
from typing import Dict, Any, Optional

import requests
import config
# from common import retry_api_call # 削除

from core.logger import setup_logging   # 修正: core.loggerを使用
from models.switchbot import DeviceStatusResponse
from services import notification_service

logger = setup_logging("service.switchbot")

DEVICE_NAME_CACHE: Dict[str, str] = {}
# fetch_device_name_cache() の呼出元がどこにも無く DEVICE_NAME_CACHE が常に空のままだった
# 問題(#411 S-L2)への対応。lifespan からの明示的な事前ロードは行わず、
# get_device_name_by_id() の初回呼出し(＝最初のWebhook受信)時に一度だけ遅延ロードする。
# Issue #533: 以前は bool(_fetch_attempted)で「一度でも試みたら二度と取得しない」だったため、
# 最初の Webhook 到着時に SwitchBot API/DNS が落ちていると(起動直後・ネットワーク瞬断で
# 起きやすい)devices.json に無いデバイスは再起動まで Unknown_<mac> のままだった。
# 最終試行時刻(monotonic)を持ち、DEVICE_NAME_FETCH_RETRY_SEC 経過後に再試行する。
_last_fetch_attempt_at: Optional[float] = None
DEVICE_NAME_FETCH_RETRY_SEC: float = 600.0
# #439: DEVICE_NAME_CACHE/_last_fetch_attempt_at は複数のWebhookリクエストスレッドから
# 並行してアクセスされうる。「キャッシュが空か確認してから_last_fetch_attempt_atを更新する」
# チェックのタイミングが重なると、初回リクエストが集中した際にAPI呼び出しが
# 複数回走ってしまうため、このLockで保護する(APIコール自体はLock外で行う)。
_device_cache_lock = threading.Lock()

def request_switchbot_api(url: str, headers: Dict[str, str], max_retries: int = 4) -> Optional[Dict[str, Any]]:
    """SwitchBot APIへのリクエスト（Exponential Backoff リトライ付き）"""
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=10.0)
            response.raise_for_status()
            
            raw_data = response.json()
            validated = DeviceStatusResponse(**raw_data)
            return validated.model_dump()
            
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            # ERRORではなくWARNINGとし、Tracebackは出さない
            logger.warning(f"⚠️ SwitchBot API connection issue (Attempt {attempt + 1}/{max_retries}): {e}")
            
        except requests.exceptions.RequestException as e:
            # 認証エラー(401)などの致命的なものはERRORとして扱う
            logger.error(f"❌ SwitchBot API fatal error: {e}")
            break
            
        # Exponential Backoff の適用
        if attempt < max_retries - 1:
            backoff_time = 2 ** attempt  # 1s, 2s, 4s...
            logger.debug(f"Retrying in {backoff_time} seconds...")
            time.sleep(backoff_time)
            
    # Fail-Soft: 最終的に失敗した場合は None を返し、システムを止めない
    logger.warning("⚠️ SwitchBot API completely failed after retries. Operating in Fail-Soft mode.")
    return None


def post_switchbot_api(url: str, headers: Dict[str, str], json_data: Dict[str, Any]) -> Dict[str, Any]:
    """SwitchBot APIへのPOSTリクエスト（コマンド動作用。単発・リトライなし）。

    #661: docstring に「リトライ付き」とあったが実装は単発の `requests.post` で、
    リトライを行うのは GET 側の `request_switchbot_api` だけ。コマンド送信は
    「消灯を2回送る」等の二重実行が副作用として現れうるため、単発のままにして
    説明の方を実装に合わせた(リトライを入れる場合は冪等性の検討とセットで行うこと)。
    """
    response = requests.post(url, headers=headers, json=json_data, timeout=10)
    response.raise_for_status()
    # コマンド送信レスポンスは汎用的なJSONが返るため、モデルバリデーションは行わずに返す
    return response.json()

def send_device_command(device_id: str, command: str, parameter: str = "default", command_type: str = "command") -> Optional[Dict[str, Any]]:
    """指定されたデバイスにコマンドを送信する"""
    url = f"{config.SWITCHBOT_API_HOST}/v1.1/devices/{device_id}/commands"
    headers = create_switchbot_auth_headers()
    if not headers:
        return None
        
    payload = {
        "command": command,
        "parameter": parameter,
        "commandType": command_type
    }
    
    try:
        response_data = post_switchbot_api(url, headers, payload)
        return response_data
    except Exception as e:
        logger.error(f"Failed to send command [{command}] to device [ID:{device_id}]: {e}")
        return None

def trigger_tv_unlock(context: str) -> None:
    """TVプラグの電源をONにする(非同期・Fail-Soft)。

    quest_service(クエスト承認時のTVロック解除)・routine_service(朝の準備
    チェックリスト全項目達成時)など、複数の呼び出し元から共有される処理。
    元は services/quest/quest_service.py の QuestService._trigger_tv_unlock
    だったが、routine_service側でも同じ処理が必要になったためこちらへ切り出した。
    呼び出し元は事前に config.TV_PLUG_DEVICE_ID が設定されているか確認すること
    (本関数自体は未設定時のガードを持たない、切り出し前の挙動を踏襲)。
    `context`はログ出力にのみ使う識別用の文字列(例: "quest_id=101")。
    """
    def unlock_task():
        logger.info(f"📺 Initiating TV Unlock (Turn ON) for {context}")
        try:
            res = send_device_command(config.TV_PLUG_DEVICE_ID, "turnOn")
            if res and res.get("statusCode") == 100:
                logger.info("✅ TV Unlock successful.")
            else:
                raise Exception(f"API returned error: {res}")
        except Exception as e:
            logger.error(f"❌ TV Unlock failed: {e}")
            # Fail-Soft: エラー時は親グループへ通知
            if config.LINE_PARENTS_GROUP_ID:
                msg = "⚠️ テレビの電源ON（自動ロック解除）に失敗しました。お手数ですが、SwitchBotアプリ等から手動でつけてあげてください。"
                notification_service.send_push(
                    user_id=config.LINE_PARENTS_GROUP_ID,
                    messages=[{"type": "text", "text": msg}]
                )

    # APIコールでAPIルーティング（メインスレッド）をブロックしないよう非同期で実行
    t = threading.Thread(target=unlock_task, daemon=True)
    t.start()


def create_switchbot_auth_headers() -> Dict[str, str]:
    """認証ヘッダーを生成する関数"""
    token = config.SWITCHBOT_API_TOKEN
    secret = config.SWITCHBOT_API_SECRET
    
    # 修正: 型安全のため明示的にエンコード
    if not token or not secret:
        logger.warning("SwitchBot Token/Secret is missing in config.")
        return {}

    t = int(round(time.time() * 1000))
    nonce = uuid.uuid4().hex
    string_to_sign = '{}{}{}'.format(token, t, nonce)
    
    secret_bytes = bytes(secret, 'utf-8')
    string_to_sign_bytes = bytes(string_to_sign, 'utf-8')
    
    sign = base64.b64encode(
        hmac.new(secret_bytes, string_to_sign_bytes, digestmod=hashlib.sha256).digest()
    )
    
    return {
        'Authorization': token,
        'sign': str(sign, 'utf-8'),
        't': str(t),
        'nonce': nonce,
        'Content-Type': 'application/json; charset=utf8'
    }

def fetch_device_name_cache() -> bool:
    """全デバイスの名前を取得してメモリに記憶する関数"""
    global DEVICE_NAME_CACHE
    logger.info("SwitchBotデバイスリストを取得中...") # 修正: print -> logger
    
    try:
        url = "https://api.switch-bot.com/v1.1/devices"
        headers = create_switchbot_auth_headers()
        if not headers:
            return False

        res = request_switchbot_api(url, headers)

        # Fail-Soft対応: APIがNoneを返した場合はFalseとして安全に終了
        if not res:
            return False
        
        # statusCodeのチェックは request_switchbot_api 内のPydanticモデルでも行われるが念のため
        if res.get('statusCode') == 100:
            body = res.get('body', {})
            new_names: Dict[str, str] = {}
            # 通常デバイス
            for d in body.get('deviceList', []):
                new_names[d['deviceId']] = d['deviceName']
            # 赤外線デバイス
            for d in body.get('infraredRemoteList', []):
                new_names[d['deviceId']] = d['deviceName']

            with _device_cache_lock:
                DEVICE_NAME_CACHE.update(new_names)
                cache_size = len(DEVICE_NAME_CACHE)
            logger.info(f"✅ {cache_size} 個のデバイス名をキャッシュしました。") # 修正: print -> logger
            return True
        else:
            logger.error(f"SwitchBot API Error: {res}")
            return False

    except Exception as e:
        logger.error(f"デバイスリスト取得失敗: {e}", exc_info=True)
        return False

def get_device_name_by_id(device_id: str) -> Optional[str]:
    """IDから名前を検索する関数。キャッシュが空ならここで一度だけ遅延ロードを試みる。"""
    global _last_fetch_attempt_at
    with _device_cache_lock:
        now = time.monotonic()
        retry_due = (
            _last_fetch_attempt_at is None
            or (now - _last_fetch_attempt_at) >= DEVICE_NAME_FETCH_RETRY_SEC
        )
        should_fetch = not DEVICE_NAME_CACHE and retry_due
        if should_fetch:
            _last_fetch_attempt_at = now
    if should_fetch:
        # APIリクエスト(ネットワークI/O)は_device_cache_lock保持中に行わない
        fetch_device_name_cache()
    with _device_cache_lock:
        return DEVICE_NAME_CACHE.get(device_id, None)

def get_device_status(device_id: str) -> Optional[Dict[str, Any]]:
    """
    指定されたデバイスの最新ステータスを取得する
    """
    url = f"{config.SWITCHBOT_API_HOST}/v1.1/devices/{device_id}/status"
    headers = create_switchbot_auth_headers()
    
    try:
        # request_switchbot_api は既存の関数を使用
        response_data = request_switchbot_api(url, headers)
        return response_data
    except Exception as e:
        logger.error(f"Failed to get device status [ID:{device_id}]: {e}")
        return None