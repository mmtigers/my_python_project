# HOME_SYSTEM/switchbot_webhook_refresh.py
import requests
import json
import switchbot_get_device_list as sb_tool
import config
import sys
import time

# ★★★ あなたの現在の ngrok URL ★★★
YOUR_NGROK_WEBHOOK_URL = "https://18b7c0400e3d.ngrok-free.app/webhook/switchbot" 

def refresh_webhook():
    print(f"\n--- SwitchBot Webhook 強制更新 ---")
    headers = sb_tool.create_switchbot_auth_headers()
    
    # 1. 既存のWebhookを削除 (deleteWebhook)
    print("[1/2] 既存の設定を削除中...")
    url_delete = "https://api.switch-bot.com/v1.1/webhook/deleteWebhook"
    payload_delete = {
        "action": "deleteWebhook",
        "url": YOUR_NGROK_WEBHOOK_URL # URL指定が必要な場合があります
    }
    
    try:
        # 削除リクエスト
        res = requests.post(url_delete, headers=headers, json=payload_delete)
        print(f"   => 削除結果: {res.json().get('message', 'Unknown')}")
    except Exception as e:
        print(f"   => 削除エラー (無視して続行): {e}")

    time.sleep(2) # 少し待機

    # 2. 新しいURLで登録 (setupWebhook)
    print(f"[2/2] 新しいURLで登録中: {YOUR_NGROK_WEBHOOK_URL}")
    url_setup = "https://api.switch-bot.com/v1.1/webhook/setupWebhook"
    payload_setup = {
        "action": "setupWebhook",
        "url": YOUR_NGROK_WEBHOOK_URL,
        "deviceList": "ALL"
    }
    
    try:
        res = requests.post(url_setup, headers=headers, json=payload_setup)
        data = res.json()
        print(f"   => ステータス: {data.get('statusCode')}")
        print(f"   => メッセージ: {data.get('message')}")
        
        if data.get('statusCode') == 100:
            print("\n✅ 成功！Webhook URLが最新のngrokアドレスに更新されました。")
        else:
            print("\n⚠️ 注意: 登録に失敗したか、既に同一URLが登録されています。")

    except Exception as e:
        print(f"[ERROR] {e}")

if __name__ == "__main__":
    refresh_webhook()