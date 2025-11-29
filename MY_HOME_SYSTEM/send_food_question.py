# HOME_SYSTEM/send_food_question.py
import config
import datetime
import pytz
import common # ★共通ライブラリ

def check_if_already_logged():
    """今日の夕食が記録済みかチェック"""
    conn = common.get_db_connection()
    if not conn: return False
    
    try:
        today_str = common.get_today_date_str()
        query = f"SELECT COUNT(*) FROM {config.SQLITE_TABLE_FOOD} WHERE meal_date=? AND meal_time_category='Dinner'"
        cursor = conn.cursor()
        cursor.execute(query, (today_str,))
        count = cursor.fetchone()[0]
        return count > 0
    except Exception as e:
        print(f"[ERROR] DBチェック失敗: {e}")
        return False
    finally:
        conn.close()

def send_food_question():
    print("[INFO] 食事質問処理を開始...")

    # 二重質問チェック
    if check_if_already_logged():
        print("[INFO] 記録済みのためスキップします。")
        return

    # 挨拶
    now = datetime.datetime.now(pytz.timezone("Asia/Tokyo"))
    greeting = "こんばんは、ご主人様！お疲れ様です。" if 17 <= now.hour <= 23 else "こんにちは！"
    message_text = f"🍽️ {greeting}\n今日の夕食はどうされましたか？\nカテゴリを選んで記録しましょう。"
    
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