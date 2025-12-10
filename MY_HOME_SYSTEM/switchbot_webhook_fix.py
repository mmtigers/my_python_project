# HOME_SYSTEM/switchbot_webhook_fix.py
import requests
import time
import switchbot_get_device_list as sb_tool
import common
import config

# ロガー設定
logger = common.setup_logging("webhook_fix")

def get_ngrok_url():
    """ローカルで動いているngrokから現在のURLを取得する"""
    try:
        res = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=5)
        data = res.json()
        tunnels = data.get("tunnels", [])
        for t in tunnels:
            if t.get("proto") == "https":
                return t.get("public_url")
    except Exception as e:
        logger.error(f"ngrok URL取得失敗: {e}")
    return None

def update_switchbot_webhook(base_url):
    """SwitchBotのWebhook URLを更新"""
    target_url = f"{base_url}/webhook/switchbot"
    logger.info(f"--- [SwitchBot] 更新処理: {target_url} ---")
    
    headers = sb_tool.create_switchbot_auth_headers()
    
    # 1. 現在の設定を確認
    try:
        query = requests.post("https://api.switch-bot.com/v1.1/webhook/queryWebhook", headers=headers, json={"action": "queryUrl"}).json()
        urls = query.get('body', {}).get('urls', [])
        
        if target_url in urls:
            logger.info("✅ SwitchBotは既に正しいURLです。")
            return True

        # 古い設定を削除
        for old_url in urls:
            logger.info(f"削除中: {old_url}")
            requests.post("https://api.switch-bot.com/v1.1/webhook/deleteWebhook", headers=headers, json={"action": "deleteWebhook", "url": old_url})
            time.sleep(1)
            
    except Exception as e:
        logger.error(f"SwitchBot確認エラー: {e}")

    # 2. 新しいURLを登録
    try:
        headers = sb_tool.create_switchbot_auth_headers() # ヘッダー再生成
        res = requests.post("https://api.switch-bot.com/v1.1/webhook/setupWebhook", headers=headers, json={
            "action": "setupWebhook",
            "url": target_url,
            "deviceList": "ALL"
        })
        if res.json().get('statusCode') == 100:
            logger.info("✅ SwitchBot 更新成功！")
            return True
    except Exception as e:
        logger.error(f"SwitchBot登録エラー: {e}")
    
    return False

def update_line_webhook(base_url):
    """LINE BotのWebhook URLを更新"""
    target_url = f"{base_url}/callback/line"
    logger.info(f"--- [LINE] 更新処理: {target_url} ---")

    url = "https://api.line.me/v2/bot/channel/webhook/endpoint"
    headers = {
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {"endpoint": target_url}

    try:
        res = requests.put(url, headers=headers, json=payload, timeout=10)
        if res.status_code == 200:
            logger.info("✅ LINE Bot 更新成功！")
            return True
        else:
            logger.error(f"LINE更新失敗: {res.status_code} {res.text}")
            return False
    except Exception as e:
        logger.error(f"LINE接続エラー: {e}")
        return False

def fix_all_webhooks():
    logger.info("=== Webhook 自動修復ツール起動 ===")
    
    # 1. ngrokのURLを取得
    base_url = get_ngrok_url()
    if not base_url:
        logger.error("❌ ngrokが起動していないか、URLが取得できません。")
        return

    logger.info(f"現在のベースURL: {base_url}")

    # 2. 両方のサービスを更新
    sb_result = update_switchbot_webhook(base_url)
    line_result = update_line_webhook(base_url)

    # 3. 結果通知 (Discord)
    if sb_result and line_result:
        msg = f"🔄 システム再起動完了\n\n✅ SwitchBot\n✅ LINE Bot\n\n新しいURLで待機中:\n{base_url}"
    else:
        msg = f"⚠️ システム再起動 (一部失敗)\nSwitchBot: {'OK' if sb_result else 'NG'}\nLINE: {'OK' if line_result else 'NG'}\n\nログを確認してください。"
    
    common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], target="discord")

if __name__ == "__main__":
    fix_all_webhooks()