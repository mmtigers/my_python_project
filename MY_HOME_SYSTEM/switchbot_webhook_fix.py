# HOME_SYSTEM/switchbot_webhook_super_fix.py
import requests
import json
import switchbot_get_device_list as sb_tool
import time

# ★ 今回適用したい新しいURL
TARGET_URL = "https://e622da19e8fa.ngrok-free.app/webhook/switchbot"

def super_fix_webhook():
    print(f"\n--- SwitchBot Webhook 完全修復 ---")
    headers = sb_tool.create_switchbot_auth_headers()
    
    # === ステップ1: 現在の設定を確認 ===
    print("[1/3] 現在の登録状況を確認中...")
    query_url = "https://api.switch-bot.com/v1.1/webhook/queryWebhook"
    try:
        res = requests.post(query_url, headers=headers, json={"action": "queryUrl"})
        data = res.json()
        current_urls = data.get('body', {}).get('urls', [])
        print(f"   => 現在のURL: {current_urls}")
    except Exception as e:
        print(f"   => 確認エラー: {e}")
        return

    # === ステップ2: 古いURLを全て削除 ===
    # 登録されているURLがあれば、一つずつ明示的に削除します
    if current_urls:
        print("[2/3] 古い設定を削除します...")
        del_url = "https://api.switch-bot.com/v1.1/webhook/deleteWebhook"
        
        for old_url in current_urls:
            # 削除リクエスト
            print(f"   => 削除対象: {old_url}")
            try:
                # URLを指定して削除
                payload = {"action": "deleteWebhook", "url": old_url}
                res = requests.post(del_url, headers=headers, json=payload)
                print(f"      結果: {res.json().get('message')}")
            except Exception as e:
                print(f"      エラー: {e}")
            time.sleep(1)
    else:
        print("[2/3] 削除する設定はありませんでした。")

    time.sleep(2) # 念のため待機

    # === ステップ3: 新しいURLを登録 ===
    print(f"[3/3] 新しいURLを登録します: {TARGET_URL}")
    setup_url = "https://api.switch-bot.com/v1.1/webhook/setupWebhook"
    payload = {
        "action": "setupWebhook",
        "url": TARGET_URL,
        "deviceList": "ALL"
    }
    
    try:
        # 新しいヘッダーを再生成（署名の時間切れ防止）
        headers = sb_tool.create_switchbot_auth_headers()
        res = requests.post(setup_url, headers=headers, json=payload)
        data = res.json()
        
        print(f"   => ステータス: {data.get('statusCode')}")
        print(f"   => メッセージ: {data.get('message')}")
        
        if data.get('statusCode') == 100:
            print("\n✅ 完全修復完了！URLが正しく更新されました。")
        else:
            print("\n❌ 登録失敗。ログを確認してください。")

    except Exception as e:
        print(f"[ERROR] 登録エラー: {e}")

if __name__ == "__main__":
    super_fix_webhook()