# HOME_SYSTEM/check_plug_raw_data.py
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

def check_plugs():
    print("--- SwitchBot Plug 生データ確認 ---")
    headers = create_header()
    
    # 1. デバイスリスト取得
    print("デバイスリストを取得中...")
    res = requests.get("https://api.switch-bot.com/v1.1/devices", headers=headers).json()
    
    if res.get('statusCode') != 100:
        print(f"エラー: {res}")
        return

    # 2. プラグを探してステータスを表示
    device_list = res['body']['deviceList']
    for device in device_list:
        if "Plug" in device['deviceType']:
            name = device['deviceName']
            id = device['deviceId']
            print(f"\n🔌 デバイス発見: {name} ({id})")
            
            # ステータス取得
            status_url = f"https://api.switch-bot.com/v1.1/devices/{id}/status"
            status_res = requests.get(status_url, headers=create_header()).json()
            
            print("   ▼ APIからの返信データ:")
            print(json.dumps(status_res, indent=4, ensure_ascii=False))
            
            # 答え合わせ
            body = status_res.get('body', {})
            if 'power' in body:
                print(f"   ✅ 正解キー 'power' があります！ (値: {body['power']}W)")
            else:
                print("   ❌ 'power' キーが見当たりません...")
                
            if 'weight' in body:
                print(f"   ❓ 'weight' キーがあります (値: {body['weight']})")
            else:
                print("   ℹ️ 'weight' キーはありません (やはりコードの間違いです)")

if __name__ == "__main__":
    check_plugs()