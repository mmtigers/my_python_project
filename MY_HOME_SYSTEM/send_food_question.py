# HOME_SYSTEM/send_food_question.py
import config
import common
import datetime
import pytz

logger = common.setup_logging("food_question")

def get_daily_summary():
    with common.get_db_cursor() as cursor:
        if not cursor: return ""
        try:
            today = common.get_today_date_str()
            cursor.execute(f"SELECT device_name, device_type, power_watts FROM {config.SQLITE_TABLE_SENSOR} WHERE timestamp LIKE ? AND power_watts IS NOT NULL", (f"{today}%",))
            
            tv_cnt, rice, total_w = 0, False, 0
            for row in cursor.fetchall():
                if "テレビ" in row["device_name"] and row["power_watts"] > 20: tv_cnt += 1
                if "炊飯器" in row["device_name"] and row["power_watts"] > 5: rice = True
                if row["device_type"] == "Nature Remo E Lite": total_w += row["power_watts"]
            
            summary = []
            if tv_cnt > 0: summary.append(f"📺 テレビ: 約{tv_cnt*5/60:.1f}時間見てたね")
            if rice: summary.append("🍚 ご飯: ちゃんと炊けてるよ")
            if total_w > 0:
                kwh = total_w * 5 / 60 / 1000
                summary.append(f"⚡ 今日の電気代: 約{int(kwh*31)}円くらいかな")
            return "\n".join(summary) + "\n\n" if summary else ""
        except Exception as e:
            logger.error(f"集計失敗: {e}")
            return ""

if __name__ == "__main__":
    logger.info("質問送信開始...")
    report = get_daily_summary()
    
    actions = [
        ("🏠 自炊", "食事カテゴリ_自炊"), ("🍜 外食", "食事カテゴリ_外食"),
        ("🍱 その他", "食事カテゴリ_その他"), ("スキップ", "食事_スキップ")
    ]
    items = [{"type": "action", "action": {"type": "message", "label": l, "text": t}} for l, t in actions]
    
    # 日付チェック
    now = datetime.datetime.now(pytz.timezone("Asia/Tokyo"))
    target_platform = "discord" if now.year < 2026 else "line"
    note = "\n(今はDiscordモードだよ！)" if target_platform == "discord" else ""

    msg = {
        "type": "text",
        "text": f"🌙 こんばんは、お疲れ様！\n\n{report}今日の夕食はどうしたの？{note}",
        "quickReply": {"items": items}
    }
    
    if common.send_push(config.LINE_USER_ID, [msg], target=target_platform):
        logger.info("送信完了✨")
    else:
        logger.error("送信失敗💦")