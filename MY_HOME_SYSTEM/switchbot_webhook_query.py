# HOME_SYSTEM/switchbot_webhook_query.py
import requests
import json
import switchbot_get_device_list as sb_tool
import config

def query_webhook():
    print(f"\n--- SwitchBot Webhook 設定確認 ---")
    headers = sb_tool.create_switchbot_auth_headers()
    
    # 登録状況を確認するAPI (queryWebhook)
    url = "https://api.switch-bot.com/v1.1/webhook/queryWebhook"
    payload = {
        "action": "queryUrl"
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        data = response.json()
        
        print(f"[API] ステータス: {data.get('statusCode')}")
        
        # 登録されているURLを表示
        urls = data.get('body', {}).get('urls', [])
        print(f"[API] 現在登録されているURL一覧:")
        for u in urls:
            print(f"   - {u}")

    except Exception as e:
        print(f"[ERROR] {e}")

if __name__ == "__main__":
    query_webhook()