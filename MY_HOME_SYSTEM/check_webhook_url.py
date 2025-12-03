# HOME_SYSTEM/check_webhook_url.py
import requests
import json
import switchbot_get_device_list as sb_tool
import config

def check_current_webhook():
    print("--- SwitchBot Webhook 設定確認 ---")
    
    # APIエンドポイント (設定照会用)
    url = "https://api.switch-bot.com/v1.1/webhook/queryWebhook"
    
    # 認証ヘッダー
    headers = sb_tool.create_switchbot_auth_headers()
    
    # アクション
    payload = {
        "action": "queryUrl"
    }
    
    try:
        # 確認リクエスト送信
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        data = response.json()
        
        print(f"[API Response] Status: {data.get('statusCode')}")
        
        # 登録されているURLを表示
        urls = data.get('body', {}).get('urls', [])
        print("\n=== 現在登録されているURL ===")
        if not urls:
            print("❌ 登録なし (None)")
        else:
            for u in urls:
                print(f"👉 {u}")
                
    except Exception as e:
        print(f"[ERROR] 確認失敗: {e}")

if __name__ == "__main__":
    check_current_webhook()