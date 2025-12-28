import common
import config

print("--- Discord Notify Channel Test ---")
print(f"Report URL (OK): {config.DISCORD_WEBHOOK_REPORT and config.DISCORD_WEBHOOK_REPORT[:30]}...")
print(f"Notify URL (??): {config.DISCORD_WEBHOOK_NOTIFY}")
print(f"Legacy URL (??): {config.DISCORD_WEBHOOK_URL}")

if not config.DISCORD_WEBHOOK_NOTIFY and not config.DISCORD_WEBHOOK_URL:
    print("\n❌ 原因特定: 'DISCORD_WEBHOOK_NOTIFY' が .env に設定されていません！")
else:
    print("\n📨 送信テスト中...")
    if common.send_push(config.LINE_USER_ID, [{"type": "text", "text": "🔔 テスト通知: Notifyチャンネル設定は正常です"}], target="discord", channel="notify"):
        print("✅ 送信成功: 設定は合っています。別の原因です。")
    else:
        print("❌ 送信失敗: URLが間違っている可能性があります。")
