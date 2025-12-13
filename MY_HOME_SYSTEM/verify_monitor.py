# HOME_SYSTEM/verify_monitor.py
import os
import sys

# パスを通す
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import switchbot_power_monitor

print("🚀 --- SwitchBot Monitor Verification Start ---")
print("リファクタリング後のスクリプトをロードしました。")
print("1回分の監視プロセスを実行します...")
print("-" * 30)

try:
    # main関数を直接呼び出して動作確認
    switchbot_power_monitor.main()
    print("-" * 30)
    print("✅ 実行完了: エラーなく終了しました。")
except Exception as e:
    print("-" * 30)
    print(f"❌ 実行エラー: {e}")
    import traceback
    traceback.print_exc()

print("🚀 --- Verification End ---")