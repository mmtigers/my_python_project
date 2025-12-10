# HOME_SYSTEM/check_tv_power.py
import sqlite3
import datetime
import pytz
import os

# データベースの場所
DB_PATH = "home_system.db"

def check_tv():
    if not os.path.exists(DB_PATH):
        print(f"❌ エラー: データベースが見つかりません: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 今日の日付 (JST)
    today = datetime.datetime.now(pytz.timezone("Asia/Tokyo")).strftime("%Y-%m-%d")
    print(f"🔎 調査対象日: {today}")
    print(f"📂 データベース: {DB_PATH}")

    # データの取得
    query = "SELECT timestamp, power_watts FROM device_records WHERE device_name LIKE '%テレビ%' AND timestamp LIKE ? ORDER BY timestamp"
    cursor.execute(query, (f"{today}%",))
    rows = cursor.fetchall()

    print(f"📊 取得件数: {len(rows)} 件")
    print("-" * 50)
    print("時刻                  | 電力(W) | 判定(>20W)")
    print("-" * 50)

    on_count = 0
    max_watts = 0.0

    for ts, watts in rows:
        # None対策
        if watts is None: watts = 0.0
        
        # 最大値更新
        if watts > max_watts: max_watts = watts

        # 判定ロジック (20W以上でON)
        is_on = watts > 20
        mark = "✅ ON " if is_on else "   ---"
        
        if is_on: on_count += 1
        
        # ログ表示 (全て出すと多すぎる場合は、0W以外を表示するなど調整可)
        # 今回は徹底調査なので全て出しますが、見やすく整形
        time_str = ts[11:16] # HH:MM だけ抽出
        print(f"{ts[:10]} {time_str} | {watts:5.1f} W | {mark}")

    print("-" * 50)
    print(f"📈 今日の最大電力: {max_watts} W")
    print(f"💡 ON判定回数    : {on_count} 回")
    print(f"📺 推定視聴時間  : {on_count * 5} 分 ({on_count * 5 / 60:.1f} 時間)")
    
    if max_watts > 0 and max_watts <= 20:
        print("\n⚠️ 【原因の可能性】")
        print("テレビの電力は検知されていますが、すべて「20W以下」です。")
        print("send_food_question.py の判定基準 (20W) が厳しすぎるかもしれません。")
        print("判定基準を 10W や 5W に下げることを検討してください。")
    elif max_watts == 0:
        print("\n⚠️ 【原因の可能性】")
        print("一日中「0.0W」のままです。")
        print("1. SwitchBotプラグが正しく挿さっていない")
        print("2. SwitchBotプラグ自体がオフになっている (物理ボタンを確認)")
        print("3. テレビの主電源が切れている")

if __name__ == "__main__":
    check_tv()