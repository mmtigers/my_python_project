# HOME_SYSTEM/switchbot_service.py
import requests
import time
import hashlib
import hmac
import base64
import uuid
import config 
import common

DEVICE_NAME_CACHE = {}

@common.retry_api_call
def request_switchbot_api(url, headers):
    """SwitchBot APIへのリクエスト（リトライ付き）"""
    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status() # 4xx, 5xxエラー時に例外を投げてリトライをトリガー
    return response.json()

def create_switchbot_auth_headers():
    """認証ヘッダーを生成する関数"""
    token = config.SWITCHBOT_API_TOKEN
    secret = config.SWITCHBOT_API_SECRET
    t = int(round(time.time() * 1000))
    nonce = uuid.uuid4().hex
    string_to_sign = '{}{}{}'.format(token, t, nonce)
    sign = base64.b64encode(hmac.new(bytes(secret, 'utf-8'), bytes(string_to_sign, 'utf-8'), digestmod=hashlib.sha256).digest())
    
    return {
        'Authorization': token,
        'sign': str(sign, 'utf-8'),
        't': str(t),
        'nonce': nonce,
        'Content-Type': 'application/json; charset=utf8'
    }

def fetch_device_name_cache():
    """全デバイスの名前を取得してメモリに記憶する関数"""
    global DEVICE_NAME_CACHE
    print("[INFO] SwitchBotデバイスリストを取得中...")
    try:
        url = "https://api.switch-bot.com/v1.1/devices"
        headers = create_switchbot_auth_headers()
        res = request_switchbot_api(url, headers)
        if res.get('statusCode') == 100:
            # 通常デバイス
            for d in res['body']['deviceList']: 
                DEVICE_NAME_CACHE[d['deviceId']] = d['deviceName']
            # 赤外線デバイス
            for d in res['body']['infraredRemoteList']: 
                DEVICE_NAME_CACHE[d['deviceId']] = d['deviceName']
            
            print(f"[SUCCESS] {len(DEVICE_NAME_CACHE)} 個のデバイス名をキャッシュしました。")
            return True
    except Exception as e:
        print(f"[ERROR] デバイスリスト取得失敗: {e}")
    return False

def get_device_name_by_id(device_id):
    """IDから名前を検索する関数"""
    return DEVICE_NAME_CACHE.get(device_id, None)

