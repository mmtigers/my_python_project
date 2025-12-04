# HOME_SYSTEM/send_food_question.py
import config
import common

# ロガー
logger = common.setup_logging("food_question")

def get_daily_summary():
    """今日の家電稼働状況と総消費電力を集計"""
    with common.get_db_cursor() as cursor:
        if not cursor: return ""
        try:
            today = common.get_today_date_str()
            sql = f"""
                SELECT device_name, device_type, power_watts 
                FROM {config.SQLITE_TABLE_SENSOR} 
                WHERE timestamp LIKE ? AND power_watts IS NOT NULL
            """
            cursor.execute(sql, (f"{today}%",))
            
            tv_on_count = 0
            rice_cooked = False
            total_watts_sum = 0
            
            for row in cursor.fetchall():
                name = row["device_name"]
                dtype = row["device_type"]
                power = row["power_watts"]
                
                if "テレビ" in name and power > 20: tv_on_count += 1
                if "炊飯器" in name and power > 5: rice_cooked = True
                if dtype == "Nature Remo E Lite": total_watts_sum += power
            
            summary = []
            if tv_on_count > 0:
                summary.append(f"📺 テレビ: 約{tv_on_count * 5 / 60:.1f}時間")
            if rice_cooked:
                summary.append("🍚 ご飯: 炊きました")
            if total_watts_sum > 0:
                total_kwh = total_watts_sum * 5 / 60 / 1000
                cost_yen = int(total_kwh * 31)
                summary.append(f"⚡ 今日の電気: {total_kwh:.2f}kWh (約{cost_yen}円)")
                
            return "\n".join(summary) + "\n\n" if summary else ""
        except Exception as e:
            logger.error(f"集計失敗: {e}")
            return ""

if __name__ == "__main__":
    logger.info("質問送信処理を開始...")
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
    
    # 修正: send_line_push -> send_push
    if common.send_push(config.LINE_USER_ID, [msg]):
        logger.info("送信完了")
    else:
        logger.error("送信失敗")