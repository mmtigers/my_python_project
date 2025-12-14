# verify_morning_check.py
import sys
import os
import datetime
import importlib

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "MY_HOME_SYSTEM"))

try:
    from MY_HOME_SYSTEM import send_child_health_check
    from MY_HOME_SYSTEM import config
except ImportError:
    import send_child_health_check
    import config

def run_test():
    print("🧪 [Test] 朝の記念日チェック機能の検証...")
    
    # configのリロード（JSON読み込みを確実にするため）
    importlib.reload(config)
    print(f"📂 読み込んだ記念日データ: {len(config.IMPORTANT_DATES)} 件")

    # テストケース
    test_cases = [
        {
            "date": datetime.datetime(2025, 3, 3), 
            "desc": "将博さん誕生日(3/3) & ゾロ目"
        },
        {
            "date": datetime.datetime(2025, 6, 14), 
            "desc": "結婚記念日(6/14)"
        },
        {
            "date": datetime.datetime(2025, 12, 10), 
            "desc": "通常日"
        }
    ]

    for case in test_cases:
        dummy_today = case["date"]
        print(f"\n📅 ケース: {case['desc']} ({dummy_today.strftime('%Y-%m-%d')})")
        
        msg = send_child_health_check.check_special_events(dummy_today)
        
        if msg:
            print(f"   💌 生成メッセージ:\n{'-'*20}\n{msg}\n{'-'*20}")
        else:
            print("   ⚪ メッセージなし (正常)")

    print("\n🎉 検証完了")

if __name__ == "__main__":
    run_test()