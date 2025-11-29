# HOME_SYSTEM/switchbot_webhook_setup.py
import requests
import json
import switchbot_get_device_list as sb_tool
import config
import sys

# ★★★ あなたの現在の ngrok URL ★★★
YOUR_NGROK_WEBHOOK_URL = "https://18b7c0400e3d.ngrok-free.app/webhook/switchbot" 

def setup_webhook():
    print(f"\n--- SwitchBot Webhook登録 ({YOUR_NGROK_WEBHOOK_URL}) ---")

    # 1. 認証ヘッダーの生成
    headers = sb_tool.create_switchbot_auth_headers()
    
    # 2. Webhook登録用APIエンドポイント (修正箇所: setup -> setupWebhook)
    url = "https://api.switch-bot.com/v1.1/webhook/setupWebhook"
    
    # 3. リクエストボディの定義 (修正箇所: setup -> setupWebhook)
    payload = {
        "action": "setupWebhook",
        "url": YOUR_NGROK_WEBHOOK_URL,
        "deviceList": "ALL" 
    }

    # 4. 登録リクエストの実行
    try:
        response = requests.post(url, headers=headers, json=payload)
        data = response.json()
        
        print(f"[API] ステータスコード: {data.get('statusCode')}")
        print(f"[API] メッセージ: {data.get('message')}")

        if data.get('statusCode') == 100:
            print("✅ Webhookの登録/更新に成功しました！")
        else:
            print(f"❌ Webhook登録失敗: {data}")

    except Exception as e:
        print(f"[ERROR] 接続エラー: {e}")

if __name__ == "__main__":
    setup_webhook()