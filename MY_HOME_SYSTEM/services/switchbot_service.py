# MY_HOME_SYSTEM/services/switchbot_service.py
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

logger = setup_logging("service.switchbot")

DEVICE_NAME_CACHE: Dict[str, str] = {}

def request_switchbot_api(url: str, headers: Dict[str, str], max_retries: int = 4) -> Optional[Dict[str, Any]]:
    """SwitchBot APIへのリクエスト（Exponential Backoff リトライ付き）"""
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=10.0)
            response.raise_for_status()
            
            raw_data = response.json()
            validated = DeviceStatusResponse(**raw_data)
            return validated.dict()
            
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
            # 通常デバイス
            for d in body.get('deviceList', []): 
                DEVICE_NAME_CACHE[d['deviceId']] = d['deviceName']
            # 赤外線デバイス
            for d in body.get('infraredRemoteList', []): 
                DEVICE_NAME_CACHE[d['deviceId']] = d['deviceName']
            
            logger.info(f"✅ {len(DEVICE_NAME_CACHE)} 個のデバイス名をキャッシュしました。") # 修正: print -> logger
            return True
        else:
            logger.error(f"SwitchBot API Error: {res}")
            return False

    except Exception as e:
        logger.error(f"デバイスリスト取得失敗: {e}", exc_info=True)
        return False

def get_device_name_by_id(device_id: str) -> Optional[str]:
    """IDから名前を検索する関数"""
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