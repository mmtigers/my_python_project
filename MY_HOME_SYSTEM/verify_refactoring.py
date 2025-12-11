# HOME_SYSTEM/verify_refactoring.py
import os
import sys

# パスを通す
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
import common
from handlers import line_logic

def run_verification():
    print("🚀 --- Refactoring Verification Start ---")
    
    # 1. Config Check
    print(f"1. Config Load Check: BaseDir = {config.BASE_DIR}")
    if config.DISCORD_WEBHOOK_ERROR:
        print("   ✅ Discord Webhook Configured")
    else:
        print("   ⚠️ Discord Webhook MISSING (Check .env)")

    # 2. Database Check
    print("2. Database Connection Check...")
    try:
        with common.get_db_cursor() as cur:
            cur.execute("SELECT sqlite_version()")
            ver = cur.fetchone()[0]
            print(f"   ✅ SQLite Version: {ver}")
    except Exception as e:
        print(f"   ❌ DB Error: {e}")

    # 3. Logic Handler Check
    print("3. Handler Import Check...")
    if hasattr(line_logic, "process_message"):
        print("   ✅ line_logic loaded successfully")
    else:
        print("   ❌ line_logic broken")

    # 4. Notification Test
    print("4. Sending Test Notification (Discord)...")
    msg = [{"type": "text", "text": "✅ **Refactoring Verification**\nこれはリファクタリング後のテスト通知です。\n主婦にも優しい表現になっていますか？"}]
    
    # ターゲット指定テスト
    if common.send_push(config.LINE_USER_ID, msg, target="discord", channel="report"):
        print("   ✅ Discord Send OK")
    else:
        print("   ❌ Discord Send FAILED")

    print("\n🎉 --- Verification Complete ---")

if __name__ == "__main__":
    run_verification()