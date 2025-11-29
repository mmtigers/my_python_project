# HOME_SYSTEM/switchbot_webhook_fix.py
import requests
import json
import switchbot_get_device_list as sb_tool
import config
import time

# 見つかった古いURL (削除対象)
OLD_URL = "https://nish-salon.com/webhook"

# あなたの現在の ngrok URL (登録対象)
NEW_URL = "https://18b7c0400e3d.ngrok-free.app/webhook/switchbot"

def fix_webhook():
    print(f"\n--- SwitchBot Webhook 設定の修復 ---")
    headers = sb_tool.create_switchbot_auth_headers()
    
    # 1. 古いURLを削除
    print(f"[1/2] 古いURLを削除中: {OLD_URL}")
    url_delete = "https://api.switch-bot.com/v1.1/webhook/deleteWebhook"
    payload_delete = {
        "action": "deleteWebhook",
        "url": OLD_URL
    }
    
    try:
        res = requests.post(url_delete, headers=headers, json=payload_delete)
        data = res.json()
        print(f"   => ステータス: {data.get('statusCode')}")
        print(f"   => メッセージ: {data.get('message')}")
    except Exception as e:
        print(f"   => エラー: {e}")

    time.sleep(2) # 反映待ち

    # 2. 新しいURLを登録
    print(f"[2/2] 新しいURLを登録中: {NEW_URL}")
    url_setup = "https://api.switch-bot.com/v1.1/webhook/setupWebhook"
    payload_setup = {
        "action": "setupWebhook",
        "url": NEW_URL,
        "deviceList": "ALL"
    }
    
    try:
        res = requests.post(url_setup, headers=headers, json=payload_setup)
        data = res.json()
        print(f"   => ステータス: {data.get('statusCode')}")
        print(f"   => メッセージ: {data.get('message')}")
        
        if data.get('statusCode') == 100:
            print("\n✅ 修復完了！Webhook URLが正しく更新されました。")
        else:
            print("\n❌ 登録失敗。ログを確認してください。")

    except Exception as e:
        print(f"[ERROR] {e}")

if __name__ == "__main__":
    fix_webhook()