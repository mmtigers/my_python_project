# HOME_SYSTEM/send_food_question.py
import config
import common

def get_daily_summary():
    conn = common.get_db_connection()
    if not conn: return ""
    try:
        cur = conn.cursor()
        today = common.get_today_date()
        cur.execute(f"SELECT device_name, power_watts FROM {config.SQLITE_TABLE_SENSOR} WHERE timestamp LIKE ? AND power_watts IS NOT NULL", (f"{today}%",))
        
        tv_cnt, rice = 0, False
        for row in cur.fetchall():
            if "テレビ" in row["device_name"] and row["power_watts"] > 20: tv_cnt += 1
            if "炊飯器" in row["device_name"] and row["power_watts"] > 5: rice = True
            
        summary = []
        if tv_cnt > 0: summary.append(f"📺 テレビ: 約{tv_cnt*5/60:.1f}時間")
        if rice: summary.append("🍚 ご飯: 炊きました")
        return "\n".join(summary) + "\n\n" if summary else ""
    finally: conn.close()

if __name__ == "__main__":
    print("[INFO] 質問送信開始...")
    report = get_daily_summary()
    
    actions = [
        ("🏠 自炊", "食事カテゴリ_自炊"), ("🍜 外食", "食事カテゴリ_外食"),
        ("🍱 その他", "食事カテゴリ_その他"), ("スキップ", "食事_スキップ")
    ]
    items = [{"type": "action", "action": {"type": "message", "label": l, "text": t}} for l, t in actions]
    
    msg = {
        "type": "text",
        "text": f"🍽️ こんばんは！\n\n{report}今日の夕食はどうされましたか？",
        "quickReply": {"items": items}
    }
    
    if common.send_push(config.LINE_USER_ID, [msg]):
        print("[SUCCESS] 送信完了")
    else:
        print("[ERROR] 送信失敗")