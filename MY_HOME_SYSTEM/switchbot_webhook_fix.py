# MY_HOME_SYSTEM/switchbot_webhook_fix.py
import requests
import time
import switchbot_get_device_list as sb_tool
import common
import config
import sys

# ロガー設定
logger = common.setup_logging("webhook_fix")

def get_ngrok_url_with_retry(max_retries=20, delay=3):
    """
    ngrokのURLを取得する。失敗してもリトライする堅牢仕様。
    """
    logger.info("SEARCH: ngrokの起動を確認しています...")
    
    for i in range(max_retries):
        try:
            # ローカルのngrok管理画面からトンネル情報を取得
            res = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=5)
            data = res.json()
            tunnels = data.get("tunnels", [])
            
            for t in tunnels:
                if t.get("proto") == "https":
                    url = t.get("public_url")
                    if url:
                        logger.info(f"✅ FOUND: ngrok URL取得成功 ({i+1}回目): {url}")
                        return url
        except Exception:
            # 接続できない＝まだ起動していないとみなす
            pass
        
        # まだ見つからない場合
        sys.stdout.write(f"\r⏳ 待機中... ngrokの準備を待っています ({i+1}/{max_retries})")
        sys.stdout.flush()
        time.sleep(delay)
    
    print("") # 改行
    logger.error("❌ TIMEOUT: ngrokのURLが取得できませんでした。")
    return None

def update_switchbot_webhook(base_url):
    """SwitchBotのWebhook URLを更新"""
    target_url = f"{base_url}/webhook/switchbot"
    logger.info(f"🔧 [SwitchBot] 設定確認: {target_url}")
    
    headers = sb_tool.create_switchbot_auth_headers()
    
    try:
        # 1. 現在の設定を確認
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

        # 2. 新しいURLを登録
        headers = sb_tool.create_switchbot_auth_headers() # ヘッダー再生成(時間経過対策)
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
    
    # 1. ngrokのURLを取得 (最大60秒待機)
    base_url = get_ngrok_url_with_retry(max_retries=20, delay=3)
    
    if not base_url:
        msg = "😰 **システム起動失敗**\n外部との接続（ngrok）に失敗しました。\nパパに確認してもらってください💦"
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg}], target="discord", channel="error")
        sys.exit(1)

    # 2. サービス更新
    sb_result = update_switchbot_webhook(base_url)
    line_result = update_line_webhook(base_url)

    # 3. 結果通知
    # どちらかが成功していれば、システムとしては「起きた」とみなして良い
    if sb_result or line_result:
        status_text = []
        if sb_result: status_text.append("✅ 家電連携 (SwitchBot)")
        if line_result: status_text.append("✅ LINE Bot")
        
        msg_body = "✨ **システム準備OK** ✨\n\n" + "\n".join(status_text) + "\n\n今日も一日見守ります！"
        # 成功時はDiscordのレポートチャンネルへ
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg_body}], target="discord", channel="report")
    else:
        # 両方失敗
        msg_err = "⚠️ **接続設定エラー**\nURLは取得できましたが、SwitchBot/LINEへの登録に失敗しました。"
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": msg_err}], target="discord", channel="error")

if __name__ == "__main__":
    fix_all_webhooks()