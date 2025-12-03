# HOME_SYSTEM/send_food_question.py
import requests
import json
import config
import datetime
import pytz
import common

def get_daily_summary():
    """今日の家電稼働状況をDBから集計してテキスト化"""
    conn = common.get_db_connection()
    if not conn: return ""
    
    try:
        cursor = conn.cursor()
        today = common.get_today_date_str()
        # 今日の全センサーデータを取得
        sql = f"SELECT device_name, power_watts FROM {config.SQLITE_TABLE_SENSOR} WHERE timestamp LIKE ? AND power_watts IS NOT NULL"
        cursor.execute(sql, (f"{today}%",))
        rows = cursor.fetchall()
        
        # 集計
        tv_on_count = 0
        rice_cooked = False
        
        for row in rows:
            name = row["device_name"]
            power = row["power_watts"]
            
            # テレビ (20W以上をONとみなす)
            if "テレビ" in name and power > 20:
                tv_on_count += 1
            # 炊飯器 (5W以上なら炊飯とみなす)
            if "炊飯器" in name and power > 5:
                rice_cooked = True
                
        # 5分間隔なので、カウント数 * 5分 = 稼働時間(分)
        tv_minutes = tv_on_count * 5
        tv_hours = tv_minutes / 60
        
        summary = []
        if tv_minutes > 0:
            summary.append(f"📺 テレビ視聴: 約{tv_hours:.1f}時間")
        if rice_cooked:
            summary.append("🍚 ご飯: 炊きました")
            
        if not summary:
            return ""
        return "\n".join(summary) + "\n\n"
        
    except Exception as e:
        print(f"[ERROR] 集計失敗: {e}")
        return ""
    finally:
        conn.close()

def send_food_question():
    print("[INFO] 食事質問処理を開始...")

    # 今日のまとめを作成
    daily_report = get_daily_summary()

    # 挨拶
    now = datetime.datetime.now(pytz.timezone("Asia/Tokyo"))
    greeting = "こんばんは、ご主人様！お疲れ様です。"
    
    # ★ メッセージにレポートを合体
    message_text = f"🍽️ {greeting}\n\n{daily_report}今日の夕食はどうされましたか？\nカテゴリを選んで記録しましょう。"
    
    # ボタン作成
    actions = [
        ("🏠 自炊", "食事カテゴリ_自炊"),
        ("🍜 外食", "食事カテゴリ_外食"),
        ("🍱 その他(弁当等)", "食事カテゴリ_その他"),
        ("今日はスキップ", "食事_スキップ")
    ]
    items = [{"type": "action", "action": {"type": "message", "label": label, "text": text}} for label, text in actions]

    msg_payload = {
        "type": "text",
        "text": message_text,
        "quickReply": {"items": items}
    }

    if common.send_line_push(config.LINE_USER_ID, [msg_payload]):
        print("[SUCCESS] 質問を送信しました。")
    else:
        print("[ERROR] 送信失敗")

if __name__ == "__main__":
    send_food_question()