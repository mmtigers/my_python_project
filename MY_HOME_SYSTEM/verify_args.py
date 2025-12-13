import inspect
import sys
import os

# パスを通す
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import switchbot_power_monitor

print("🔍 関数定義チェック:")
# process_power_notification の引数の数を数える
sig = inspect.signature(switchbot_power_monitor.process_power_notification)
params = list(sig.parameters.keys())
print(f"  関数名: process_power_notification")
print(f"  引数リスト: {params}")
print(f"  引数の数: {len(params)}")

if len(params) == 5:
    print("\n✅ OK: ファイル上のコードは修正済みです。")
    print("👉 エラーが出る場合は、常駐プロセス(Systemd)の再起動が必要です。")
else:
    print("\n❌ NG: ファイルがまだ古いです。上書き保存してください。")