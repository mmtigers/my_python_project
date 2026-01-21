# MY_HOME_SYSTEM/switchbot_webhook_fix.py
import sys
import os
import traceback

# --- 1. 強制パス設定 (Path Injection) ---
# このファイルがある場所 (/home/masahiro/develop/MY_HOME_SYSTEM)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# その親ディレクトリ (/home/masahiro/develop)
PARENT_DIR = os.path.dirname(BASE_DIR)

# Pythonの検索パスの先頭に追加
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(1, PARENT_DIR)

print(f"🔍 DEBUG: Base Dir: {BASE_DIR}")

# --- 2. インポート試行 (Verbose Import) ---
try:
    import common
    import config
    # servicesフォルダから switchbot_service をインポート
    from services import switchbot_service as sb_tool
    print("✅ Module Loaded: switchbot_service")
except ImportError as e:
    print("\n❌ IMPORT ERROR DETECTED!")
    print(f"Reason: {e}")
    print("--- Detailed Traceback ---")
    traceback.print_exc()
    print("--------------------------")
    sys.exit(1)

import requests
import time

# ロガー設定
logger = common.setup_logging("webhook_fix")

def get_ngrok_url_with_retry(max_retries=20, delay=3):
    """ngrokのURLを取得する"""
    logger.info("SEARCH: ngrokの起動を確認しています...")
    
    for i in range(max_retries):
        try:
            res = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=5)
            data = res.json()
            tunnels = data.get("tunnels", [])
            
            for t in tunnels:
                if t.get("proto") == "https":
                    addr = t.get("config", {}).get("addr", "")
                    if "8000" in addr:
                        url = t.get("public_url")
                        if url:
                            logger.info(f"✅ FOUND: サーバー用URLを発見: {url}")
                            return url
        except Exception:
            pass
        
        sys.stdout.write(f"\r⏳ 待機中... ({i+1}/{max_retries})")
        sys.stdout.flush()
        time.sleep(delay)
    
    print("") 
    logger.error("❌ TIMEOUT: ngrokのURLが取得できませんでした。")
    return None

def update_switchbot_webhook(base_url):
    """SwitchBotのWebhook URLを更新"""
    target_url = f"{base_url}/webhook/switchbot"
    logger.info(f"🔧 [SwitchBot] 設定確認: {target_url}")
    
    headers = sb_tool.create_switchbot_auth_headers()
    
    try:
        # 現在の設定を確認
        query = requests.post("https://api.switch-bot.com/v1.1/webhook/queryWebhook", headers=headers, json={"action": "queryUrl"}).json()
        urls = query.get('body', {}).get('urls', [])
        
        if target_url in urls:
            logger.info("   ✅ 設定済みです (更新不要)")
            return True

        # 古い設定を削除
        for old_url in urls:
            logger.info(f"   🗑️ 古い設定を削除: {old_url}")
            requests.post("https://api.switch-bot.com/v1.1/webhook/deleteWebhook", headers=headers, json={"action": "deleteWebhook", "url": old_url})
            time.sleep(1)

        # 新しいURLを登録
        headers = sb_tool.create_switchbot_auth_headers()
        res = requests.post("https://api.switch-bot.com/v1.1/webhook/setupWebhook", headers=headers, json={
            "action": "setupWebhook",
            "url": target_url,
            "deviceList": "ALL"
        })
        
        if res.json().get('statusCode') == 100:
            logger.info("   ✅ 新しいURLを登録しました")
            return True
        else:
            logger.error(f"   ❌ 登録失敗: {res.text}")
            
    except Exception as e:
        logger.error(f"   ❌ SwitchBot APIエラー: {e}")
    
    return False

def update_line_webhook(base_url):
    """LINE BotのWebhook URLを更新"""
    target_url = f"{base_url}/callback/line"
    logger.info(f"🔧 [LINE] 設定確認: {target_url}")

    if not config.LINE_CHANNEL_ACCESS_TOKEN:
        logger.warning("   ⚠️ LINE Token未設定のためスキップ")
        return False

    url = "https://api.line.me/v2/bot/channel/webhook/endpoint"
    headers = {
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {"endpoint": target_url}

    try:
        res = requests.put(url, headers=headers, json=payload, timeout=10)
        if res.status_code == 200:
            logger.info("   ✅ LINE設定を更新しました")
            return True
        else:
            logger.error(f"   ❌ LINE更新失敗: {res.status_code} {res.text}")
            return False
    except Exception as e:
        logger.error(f"   ❌ LINE接続エラー: {e}")
        return False

def fix_all_webhooks():
    logger.info("🚀 Webhook自動修復ツール起動")
    
    base_url = get_ngrok_url_with_retry(max_retries=20, delay=3)
    if not base_url:
        sys.exit(1)

    sb_result = update_switchbot_webhook(base_url)
    line_result = update_line_webhook(base_url)

    if sb_result or line_result:
        msg_body = "✨ **システム準備OK** ✨\nwebhook更新完了"
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg_body}], target="discord", channel="report")

if __name__ == "__main__":
    fix_all_webhooks()