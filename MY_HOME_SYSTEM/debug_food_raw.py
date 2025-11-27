# HOME_SYSTEM/debug_food_raw.py
import requests
import json
import config

def send_debug_message():
    print("[INFO] 直接JSONデータを送信してテストします...")
    
    url = "https://api.line.me/v2/bot/message/push"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}"
    }
    
    # SDKを使わず、手動でデータ構造を作ります
    payload = {
        "to": config.LINE_USER_ID,
        "messages": [
            {
                "type": "text",
                "text": "🛠️【デバッグ】\nこれはSDKを使わずに送信しています。\nボタンは見えますか？",
                "quickReply": {
                    "items": [
                        {
                            "type": "action",
                            "action": {
                                "type": "message",
                                "label": "見えた！",
                                "text": "デバッグ成功_見えた"
                            }
                        },
                        {
                            "type": "action",
                            "action": {
                                "type": "message",
                                "label": "見えない",
                                "text": "デバッグ失敗_見えない"
                            }
                        }
                    ]
                }
            }
        ]
    }

    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        print(f"[API Response] Status: {response.status_code}")
        print(f"[API Response] Body: {response.text}")
        
        if response.status_code == 200:
            print("✅ 送信成功。スマホを確認してください。")
        else:
            print("❌ 送信エラー。トークンなどを確認してください。")
            
    except Exception as e:
        print(f"[ERROR] 接続失敗: {e}")

if __name__ == "__main__":
    send_debug_message()