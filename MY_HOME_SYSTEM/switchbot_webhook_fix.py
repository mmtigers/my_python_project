# MY_HOME_SYSTEM/switchbot_webhook_fix.py
import sys
import os
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
    """SwitchBotのWebhook URLを更新

    戻り値:
        True  - 新しいURLの登録に成功した(変更あり)
        False - 既に設定済み、またはURL照会自体に失敗し何も変更していない
        None  - 旧URLを削除した後、新URLの登録に失敗した(Issue #166: SwitchBotの
                Webhookが未設定のまま残っている危険な状態。呼び出し元は変更の
                成否に関わらずこの状態を必ず通知すること)
    """
    target_url = f"{base_url}/webhook/switchbot"
    logger.info(f"🔧 [SwitchBot] 設定確認: {target_url}")

    headers = sb_tool.create_switchbot_auth_headers()

    try:
        query = requests.post("https://api.switch-bot.com/v1.1/webhook/queryWebhook", headers=headers, json={"action": "queryUrl"}, timeout=10).json()
        urls = query.get('body', {}).get('urls', [])
    except Exception as e:
        logger.error(f"   ❌ SwitchBot APIエラー(照会): {e}")
        return False

    if target_url in urls:
        logger.info("   ✅ 設定済みです (更新不要)")
        return False # 変更なし

    # 古い設定を削除
    for old_url in urls:
        logger.info(f"   🗑️ 古い設定を削除: {old_url}")
        try:
            requests.post("https://api.switch-bot.com/v1.1/webhook/deleteWebhook", headers=headers, json={"action": "deleteWebhook", "url": old_url}, timeout=10)
        except Exception as e:
            logger.error(f"   ❌ SwitchBot APIエラー(削除): {e}")
        time.sleep(1)

    # 新しいURLを登録
    headers = sb_tool.create_switchbot_auth_headers()
    try:
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
        logger.error(f"   ❌ SwitchBot APIエラー(登録): {e}")

    # ここに到達するのは、既存設定が無い(=削除対象なし)場合も含め、対象URLが
    # 未設定のまま登録にも失敗した場合。urlsが空でない場合は旧設定を削除済みの
    # ため特に危険な状態であり、いずれにせよ呼び出し元へ「未設定」を伝える。
    return None

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
    
    # ベースURLは config.WEBHOOK_BASE_URL (環境変数 WEBHOOK_BASE_URL) から取得 (ngrok探索を廃止)。
    # #405: 以前は os.environ.get() で直接読んでいたため .env.example 整合テストの死角だった。
    base_url = config.WEBHOOK_BASE_URL
    if not base_url:
        logger.error("❌ WEBHOOK_BASE_URL が .env に設定されていません。処理を終了します。")
        sys.exit(1)

    sb_result = update_switchbot_webhook(base_url)
    line_updated = update_line_webhook(base_url)

    # update_switchbot_webhook が None(=旧設定を削除した後に新規登録が失敗し、
    # SwitchBot Webhookが未設定のまま残っている危険な状態)を返した場合は、
    # 更新の成否に関わらず必ず通知する(Issue #166: 以前はこの状態が
    # sb_updated=False に潰れてしまい、無通知のまま連携が停止していた)。
    if sb_result is None:
        alert_body = (
            "🚨 **Webhook設定エラー**\n"
            "SwitchBotの旧Webhook設定を削除しましたが、新しいURLの登録に失敗しました。\n"
            f"SwitchBotイベント連携が停止している可能性があります。手動確認が必要です。\nURL: {base_url}"
        )
        common.send_push([{"type": "text", "text": alert_body}], target="discord", channel="error")

    # 実際に更新が走った時のみ通知を送信するよう最適化
    sb_updated = bool(sb_result)
    if sb_updated or line_updated:
        msg_body = f"✨ **Webhook設定修復完了** ✨\n新しいエンドポイントに更新されました:\n{base_url}"
        common.send_push([{"type": "text", "text": msg_body}], target="discord", channel="report")

if __name__ == "__main__":
    fix_all_webhooks()