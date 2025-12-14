# investigate_takasago.py
import sqlite3
import os
import pandas as pd
from datetime import datetime
import sys

# パス設定 (config読み込み用)
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "MY_HOME_SYSTEM"))
import config

def check_takasago_status():
    print("\n🔍 --- 高砂（実家）データ徹底調査 ---")
    
    # DB接続
    db_path = config.SQLITE_DB_PATH
    if not os.path.exists(db_path):
        print(f"❌ DBファイルが見つかりません: {db_path}")
        return

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    
    # 高砂のデバイスIDリストを取得
    takasago_devices = [d for d in config.MONITOR_DEVICES if d.get('location') == '高砂']
    target_ids = [d['id'] for d in takasago_devices]
    
    if not target_ids:
        print("❌ 高砂のデバイス設定が見つかりません。config.pyを確認してください。")
        return

    print(f"📋 監視対象デバイス数: {len(target_ids)} 台")
    
    # SQL用のプレースホルダ
    placeholders = ','.join(['?'] * len(target_ids))
    
    try:
        # 1. システム生存確認 (温度計なども含む、あらゆる最新データ)
        print("\n📡 【通信チェック】 最新のデータ受信記録")
        sql_alive = f"""
            SELECT timestamp, device_name, device_type, contact_state, movement_state, temperature_celsius
            FROM {config.SQLITE_TABLE_SENSOR}
            WHERE device_id IN ({placeholders})
            ORDER BY timestamp DESC LIMIT 5
        """
        df_alive = pd.read_sql_query(sql_alive, conn, params=target_ids)
        
        if df_alive.empty:
            print("⚠️ データが全くありません。")
        else:
            latest_ts = pd.to_datetime(df_alive.iloc[0]['timestamp']).tz_convert('Asia/Tokyo')
            print(f"   最終受信: {latest_ts.strftime('%Y-%m-%d %H:%M:%S')} ({df_alive.iloc[0]['device_name']})")
            print(df_alive[['timestamp', 'device_name', 'contact_state', 'temperature_celsius']])

        # 2. 活動検知確認 (Contact=open/detected, Motion=detected)
        print("\n🏃 【活動チェック】 最新のセンサー反応 (ドア・人感)")
        sql_act = f"""
            SELECT timestamp, device_name, contact_state, movement_state
            FROM {config.SQLITE_TABLE_SENSOR}
            WHERE device_id IN ({placeholders})
            AND (contact_state IN ('open', 'detected') OR movement_state = 'detected')
            ORDER BY timestamp DESC LIMIT 5
        """
        df_act = pd.read_sql_query(sql_act, conn, params=target_ids)
        
        if df_act.empty:
            print("⚠️ 活動記録が全くありません。")
        else:
            last_act_ts = pd.to_datetime(df_act.iloc[0]['timestamp']).tz_convert('Asia/Tokyo')
            now = datetime.now(last_act_ts.tzinfo)
            diff = now - last_act_ts
            hours = diff.total_seconds() / 3600
            
            print(f"   最終活動: {last_act_ts.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"   経過時間: 約 {hours:.1f} 時間前")
            print(f"   検知デバイス: {df_act.iloc[0]['device_name']}")
            print("-" * 30)
            print(df_act)
            
            if hours >= 16:
                print("\n🚨 【結論】 確かに16時間以上、活動検知がありません。")
                print("   通信チェック(1)の日時が「最近」であれば、システムは動いていますが「動きがない」状態です。")
                print("   通信チェック(1)も古い場合は、実家のWi-FiやSwitchBotハブが落ちている可能性があります。")
            else:
                print(f"\n✅ 【結論】 直近 {hours:.1f} 時間以内に活動があります。")
                print("   ダッシュボードの表示がおかしい可能性があります (タイムゾーンやキャッシュの問題)。")

    except Exception as e:
        print(f"❌ エラー発生: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    check_takasago_status()