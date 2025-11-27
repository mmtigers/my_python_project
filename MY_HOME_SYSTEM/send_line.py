# HOME_SYSTEM/send_line.py
import requests
import json
import config

def send_push_message(message_text):
    """LINEにメッセージを送る関数"""
    url = "https://api.line.me/v2/bot/message/push"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}"
    }
    
    payload = {
        "to": config.LINE_USER_ID,
        "messages": [
            {
                "type": "text",
                "text": message_text
            }
        ]
    }

    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code == 200:
            print("[LINE] メッセージ送信成功")
            return True
        else:
            print(f"[ERROR] LINE送信失敗: {response.text}")
            return False
    except Exception as e:
        print(f"[ERROR] 接続エラー: {e}")
        return False