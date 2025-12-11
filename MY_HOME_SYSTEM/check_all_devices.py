import requests
import time
import hashlib
import hmac
import base64
import uuid
import config
import json

def create_header():
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

def check_all_devices():
    print("--- SwitchBot 全デバイス一覧取得 ---")
    headers = create_header()
    
    try:
        res = requests.get("https://api.switch-bot.com/v1.1/devices", headers=headers).json()
        if res.get('statusCode') != 100:
            print(f"エラー: {res}")
            return

        device_list = res['body']['deviceList']
        print(f"\n📦 デバイス数: {len(device_list)}")
        
        for d in device_list:
            d_type = d.get('deviceType')
            d_name = d.get('deviceName')
            d_id = d.get('deviceId')
            
            # 開閉センサー(Contact Sensor)や未登録のものを探しやすいように強調表示
            prefix = "✅" 
            if "Contact" in d_type: prefix = "🚪"
            if "Meter" in d_type: prefix = "🌡️"
            if "Plug" in d_type: prefix = "🔌"
            if "Hub" in d_type: prefix = "📡"
            
            print(f"{prefix} [{d_type}] {d_name}")
            print(f"    ID: {d_id}")
            
    except Exception as e:
        print(f"接続エラー: {e}")

if __name__ == "__main__":
    check_all_devices()