# MY_HOME_SYSTEM/switchbot_webhook_fix.py
import sys
import os
import traceback
import requests
import time

# --- 1. 強制パス設定 (Path Injection) ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(BASE_DIR)

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(1, PARENT_DIR)

try:
    import common
    import config
    from services import switchbot_service as sb_tool
except ImportError as e:
    print(f"❌ IMPORT ERROR DETECTED! Reason: {e}")
    sys.exit(1)

# ロガー設定
logger = common.setup_logging("webhook_fix")

def update_switchbot_webhook(base_url):
    """SwitchBotのWebhook URLを更新"""
    target_url = f"{base_url}/webhook/switchbot"
    logger.info(f"🔧 [SwitchBot] 設定確認: {target_url}")
    
    headers = sb_tool.create_switchbot_auth_headers()
    
    try:
        query = requests.post("https://api.switch-bot.com/v1.1/webhook/queryWebhook", headers=headers, json={"action": "queryUrl"}, timeout=10).json()
        urls = query.get('body', {}).get('urls', [])
        
        if target_url in urls:
            logger.info("   ✅ 設定済みです (更新不要)")
            return False # 変更なし

        # 古い設定を削除
        for old_url in urls:
            logger.info(f"   🗑️ 古い設定を削除: {old_url}")
            requests.post("https://api.switch-bot.com/v1.1/webhook/deleteWebhook", headers=headers, json={"action": "deleteWebhook", "url": old_url}, timeout=10)
            time.sleep(1)

        # 新しいURLを登録
        headers = sb_tool.create_switchbot_auth_headers()
        res = requests.post("https://api.switch-bot.com/v1.1/webhook/setupWebhook", headers=headers, json={
            "action": "setupWebhook",
            "url": target_url,
            "deviceList": "ALL"  # SwitchBot APIの仕様上ALL必須
        }, timeout=10)
        
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
        # 現在のエンドポイントを取得して比較（API呼び出しを節約）
        get_res = requests.get("https://api.line.me/v2/bot/channel/webhook/endpoint", headers={"Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}"}, timeout=10)
        if get_res.status_code == 200 and get_res.json().get("endpoint") == target_url:
            logger.info("   ✅ 設定済みです (更新不要)")
            return False

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
    logger.info("🚀 Webhook自動修復ツール起動 (Fixed Architecture)")
    
    # 環境変数からベースURLを取得 (ngrok探索を廃止)
    base_url = os.environ.get("WEBHOOK_BASE_URL")
    if not base_url:
        logger.error("❌ WEBHOOK_BASE_URL が .env に設定されていません。処理を終了します。")
        sys.exit(1)

    sb_updated = update_switchbot_webhook(base_url)
    line_updated = update_line_webhook(base_url)

    # 実際に更新が走った時のみ通知を送信するよう最適化
    if sb_updated or line_updated:
        msg_body = f"✨ **Webhook設定修復完了** ✨\n新しいエンドポイントに更新されました:\n{base_url}"
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg_body}], target="discord", channel="report")

if __name__ == "__main__":
    fix_all_webhooks()