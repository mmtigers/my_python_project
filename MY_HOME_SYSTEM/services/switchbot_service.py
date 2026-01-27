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
from core.network import retry_api_call # 修正: coreモジュールを使用
from core.logger import setup_logging   # 修正: core.loggerを使用
from models.switchbot import DeviceStatusResponse

logger = setup_logging("service.switchbot")

DEVICE_NAME_CACHE: Dict[str, str] = {}

@retry_api_call
def request_switchbot_api(url: str, headers: Dict[str, str]) -> Dict[str, Any]:
    """SwitchBot APIへのリクエスト（リトライ付き）"""
    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()
    
    raw_data = response.json()
    validated = DeviceStatusResponse(**raw_data)
    return validated.dict()

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