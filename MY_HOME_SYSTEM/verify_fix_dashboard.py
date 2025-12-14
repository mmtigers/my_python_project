# verify_fix_dashboard.py
import sys
import os
import importlib
import pandas as pd
from datetime import datetime, timedelta
import pytz

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import dashboard
import config

def run_test():
    print("🧪 [Test] ダッシュボード修正機能の検証...")
    
    # 1. 伊丹のMotion Sensorデータ確認
    print("\n1. 伊丹の人感センサーデータ (Motion Sensor)")
    df = dashboard.load_sensor_data(limit=1000)
    if df.empty:
        print("   ❌ データ取得失敗")
    else:
        df_motion = df[(df['location'] == '伊丹') & (df['device_type'].str.contains('Motion'))]
        if not df_motion.empty:
            latest = df_motion.iloc[0]
            print(f"   ✅ データあり: {latest['friendly_name']} ({latest['timestamp']}) - {latest['movement_state']}")
        else:
            print("   ⚠️ 伊丹のMotion Sensorデータが見つかりません (設定を確認してください)")

    # 2. 電気グラフ用データ (今日・昨日)
    print("\n2. 電気グラフ用データ範囲")
    now = datetime.now(pytz.timezone('Asia/Tokyo'))
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    
    print(f"   📅 今日範囲: {today_start} 〜")
    print(f"   📅 昨日範囲: {yesterday_start} 〜 {today_start}")
    
    # 昨日のデータがあるかチェック
    df_yst = df[
        (df['device_type'] == 'Nature Remo E Lite') & 
        (df['timestamp'] >= yesterday_start) & (df['timestamp'] < today_start)
    ]
    if not df_yst.empty:
        print(f"   ✅ 昨日のデータあり: {len(df_yst)}件 (グレー線で表示されます)")
    else:
        print("   ⚠️ 昨日のデータがありません (グラフは今日の線のみになります)")

    print("\n🎉 検証完了")

if __name__ == "__main__":
    run_test()