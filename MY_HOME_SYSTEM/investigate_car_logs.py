# HOME_SYSTEM/investigate_car_logs.py
import sqlite3
import config
import common
import datetime
import pytz

# ロガー設定
logger = common.setup_logging("investigator")

def check_db_records():
    print("\n🔍 --- 徹底調査開始 ---")
    
    # 今日の日付
    today = datetime.datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%Y-%m-%d")
    print(f"📅 調査対象日: {today}")

    with common.get_db_cursor() as cursor:
        if not cursor:
            print("❌ DBに接続できませんでした。")
            return

        # 1. 車の記録テーブル (car_records) の確認
        print(f"\n🚗 【調査1】 車の記録テーブル (car_records)")
        sql_car = f"SELECT timestamp, action, rule_name FROM {config.SQLITE_TABLE_CAR} WHERE timestamp LIKE ? ORDER BY timestamp"
        cursor.execute(sql_car, (f"{today}%",))
        car_rows = cursor.fetchall()
        
        if car_rows:
            for row in car_rows:
                print(f"  ✅ {row['timestamp']} | Action: {row['action']} | Rule: {row['rule_name']}")
        else:
            print("  ⚠️ 本日の記録はゼロです。")

        # 2. センサー生ログ (device_records) の確認
        # カメラが「何か」を検知していればここに残っているはず
        print(f"\n📷 【調査2】 カメラの全検知ログ (device_records)")
        sql_sensor = f"""
            SELECT timestamp, contact_state, device_name 
            FROM {config.SQLITE_TABLE_SENSOR} 
            WHERE device_type = 'ONVIF Camera' AND timestamp LIKE ? 
            ORDER BY timestamp
        """
        cursor.execute(sql_sensor, (f"{today}%",))
        sensor_rows = cursor.fetchall()
        
        if sensor_rows:
            print(f"  ℹ️ 合計 {len(sensor_rows)} 回の検知がありました。")
            for row in sensor_rows:
                # contact_state には "detected" や "人物" "車両" などが入る想定
                print(f"  timestamp: {row['timestamp']} | 検知内容: {row['contact_state']}")
        else:
            print("  ⚠️ カメラからの通知が一度も届いていません。")
            print("  👉 原因候補: スクリプトが止まっていた、カメラの設定ミス、ngrokのURL変更など")

    # 3. 設定の確認
    print(f"\n⚙️ 【調査3】 現在の判定キーワード設定")
    print("  外出 (LEAVE) とみなすルール名:")
    print(f"    {config.CAR_RULE_KEYWORDS['LEAVE']}")
    print("  帰宅 (RETURN) とみなすルール名:")
    print(f"    {config.CAR_RULE_KEYWORDS['RETURN']}")
    
    print("\n--------------------------------------------------")
    print("👀 ヒント:")
    print("もし【調査2】にはログがあるのに【調査1】が無い場合:")
    print("  → 検知はしていますが、「車」として認識されていないか、")
    print("    ルール名がキーワード（Exit, Enterなど）と一致していません。")
    print("--------------------------------------------------\n")

if __name__ == "__main__":
    check_db_records()