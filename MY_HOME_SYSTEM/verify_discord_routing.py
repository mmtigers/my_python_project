# verify_discord_routing.py
import common
import config

print("--- Discord チャンネル振り分けテスト ---")

# 1. レポート
print("1. レポートチャンネル (#report) への送信テスト...")
if common.send_push(config.LINE_USER_ID, [{"type": "text", "text": "📊 [テスト] これはレポートチャンネルへのテスト送信です"}], target="discord", channel="report"):
    print("✅ OK")
else:
    print("❌ NG")

# 2. エラーログ
print("2. エラーログチャンネル (#error-log) への送信テスト...")
if common.send_push(config.LINE_USER_ID, [{"type": "text", "text": "😰 [テスト] これはエラーログチャンネルへのテスト送信です"}], target="discord", channel="error"):
    print("✅ OK")
else:
    print("❌ NG")

# 3. 通知
print("3. 通知チャンネル (#notifications) への送信テスト...")
if common.send_push(config.LINE_USER_ID, [{"type": "text", "text": "🔔 [テスト] これは通知チャンネルへのテスト送信です"}], target="discord", channel="notify"):
    print("✅ OK")
else:
    print("❌ NG")