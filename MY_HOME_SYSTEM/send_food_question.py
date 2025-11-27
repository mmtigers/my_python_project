# HOME_SYSTEM/send_food_question.py
import requests
import json
import config
import datetime
import pytz

def send_food_question():
    """LINEに食事のカテゴリ質問を送信する関数 (Direct API版)"""
    print("[INFO] 食事質問の送信処理を開始します...")
    
    # LINE Messaging API のエンドポイント
    url = "https://api.line.me/v2/bot/message/push"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}"
    }
    
    # メッセージペイロードの作成（SDKを使わず辞書型で定義）
    payload = {
        "to": config.LINE_USER_ID,
        "messages": [
            {
                "type": "text",
                "text": "🍽️ 今日の夕食は何を食べましたか？\n（下のボタンをタップして記録）",
                "quickReply": {
                    "items": [
                        {
                            "type": "action",
                            "action": {
                                "type": "message",
                                "label": "和食",
                                "text": "食事_和食"
                            }
                        },
                        {
                            "type": "action",
                            "action": {
                                "type": "message",
                                "label": "洋食",
                                "text": "食事_洋食"
                            }
                        },
                        {
                            "type": "action",
                            "action": {
                                "type": "message",
                                "label": "中華",
                                "text": "食事_中華"
                            }
                        },
                        {
                            "type": "action",
                            "action": {
                                "type": "message",
                                "label": "その他",
                                "text": "食事_その他"
                            }
                        }
                    ]
                }
            }
        ]
    }

    # 送信実行
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        
        if response.status_code == 200:
            print("[SUCCESS] 食事質問を送信しました。")
            return True
        else:
            print(f"[ERROR] 送信失敗: {response.status_code} {response.text}")
            return False
            
    except Exception as e:
        print(f"[ERROR] 接続失敗: {e}")
        return False

if __name__ == "__main__":
    send_food_question()