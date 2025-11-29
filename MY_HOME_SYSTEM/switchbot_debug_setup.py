# HOME_SYSTEM/switchbot_debug_setup.py
import requests
import json
import switchbot_get_device_list as sb_tool
import config
import sys
import time
import datetime

# ★★★ あなたの ngrok URL (前回修正済み) ★★★
YOUR_NGROK_WEBHOOK_URL = "https://18b7c0400e3d.ngrok-free.app/webhook/switchbot" 

def setup_webhook_debug():
    print(f"\n--- SwitchBot Webhook Debug ({YOUR_NGROK_WEBHOOK_URL}) ---")

    # 1. ラズパイの現在時刻を表示 (重要チェックポイント)
    now = datetime.datetime.now()
    print(f"[DEBUG] ラズパイの現在時刻: {now}")
    print(f"[DEBUG] UNIXタイムスタンプ: {int(time.time() * 1000)}")

    # 2. 認証ヘッダー生成
    try:
        headers = sb_tool.create_switchbot_auth_headers()
        print(f"[DEBUG] 生成されたヘッダー (一部): Authorization={headers['Authorization'][:10]}..., t={headers['t']}")
    except Exception as e:
        print(f"[FATAL] ヘッダー生成失敗: {e}")
        return

    url = "https://api.switch-bot.com/v1.1/webhook/setup"
    payload = {
        "action": "setup",
        "url": YOUR_NGROK_WEBHOOK_URL,
        "deviceList": "ALL"
    }

    # 3. リクエスト送信 (生のレスポンスを見る)
    try:
        print("[DEBUG] リクエスト送信中...")
        response = requests.post(url, headers=headers, json=payload)
        
        # ★★★ ここが重要：ステータスコードと生データを表示 ★★★
        print(f"\n[SERVER RESPONSE] Status Code: {response.status_code}")
        print(f"[SERVER RESPONSE] Raw Body: {response.text}") 
        
        # JSON変換を試みる
        data = response.json()
        print(f"\n[SUCCESS] JSON解析成功: {data}")

    except json.JSONDecodeError:
        print("\n[ERROR] JSON変換に失敗しました。上記の Raw Body にエラーの原因が書かれています。")
    except Exception as e:
        print(f"\n[ERROR] 接続またはその他のエラー: {e}")

if __name__ == "__main__":
    setup_webhook_debug()