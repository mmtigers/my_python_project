# MY_HOME_SYSTEM/send_ai_report.py
import google.generativeai as genai
import json
import config
import common
import traceback
import argparse
import sys
from datetime import datetime
import pytz
from weather_service import WeatherService  # 天気サービスのインポート

logger = common.setup_logging("ai_report")

def get_family_profile():
    dad_name = getattr(config, "DAD_NAME", "旦那様")
    mom_name = getattr(config, "MOM_NAME", "奥様")
    children_info = ", ".join([f"{name}" for name in config.CHILDREN_NAMES]) if config.CHILDREN_NAMES else "お子様たち"
    return f"""
    - 夫: {dad_name} (仕事熱心)
    - 妻: {mom_name} (専業主婦, 家事育児に奮闘中)
    - 子供: {children_info}
    - 住まい: {getattr(config, "HOME_LOCATION", "自宅")}
    - 実家: {getattr(config, "PARENTS_LOCATION", "実家")}
    """

def parse_arguments():
    parser = argparse.ArgumentParser(description='AI日報送信スクリプト')
    parser.add_argument('--target', type=str, default='discord', choices=['line', 'discord', 'both'], help='通知先')
    return parser.parse_args()

def setup_gemini():
    if not config.GEMINI_API_KEY:
        logger.error("❌ Gemini API Keyなし")
        sys.exit(1)
    genai.configure(api_key=config.GEMINI_API_KEY)
    candidates = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash", "gemini-pro"]
    try:
        models = [m.name.replace("models/", "") for m in genai.list_models()]
        for c in candidates:
            if c in models: return genai.GenerativeModel(c)
        return genai.GenerativeModel("gemini-1.5-flash")
    except: return genai.GenerativeModel("gemini-1.5-flash")

def fetch_daily_data():
    data = {}
    today_str = common.get_today_date_str()
    with common.get_db_cursor() as cursor:
        if not cursor: raise ConnectionError("DB接続失敗")
        
        # 1. 環境
        cursor.execute(f"SELECT device_name, avg(temperature_celsius) as t, avg(humidity_percent) as h FROM {config.SQLITE_TABLE_SENSOR} WHERE timestamp LIKE ? AND device_type LIKE '%Meter%' GROUP BY device_id", (f"{today_str}%",))
        data['environment'] = [{ "place": r["device_name"], "temp": round(r["t"],1), "humidity": round(r["h"],1) } for r in cursor.fetchall()]
        
        # 2. 実家
        target_loc = getattr(config, "PARENTS_LOCATION", "高砂")
        taka_ids = [d["id"] for d in config.MONITOR_DEVICES if d.get("location") == target_loc and "Contact" in d.get("type", "")]
        if taka_ids:
            placeholders = ",".join(["?"] * len(taka_ids))
            cursor.execute(f"SELECT device_name, COUNT(*) FROM {config.SQLITE_TABLE_SENSOR} WHERE timestamp LIKE ? AND device_id IN ({placeholders}) AND contact_state IN ('open', 'detected') GROUP BY device_id", (f"{today_str}%", *taka_ids))
            data['parents_home'] = {r["device_name"]: r[1] for r in cursor.fetchall()}
        
        # 3. 電気
        cursor.execute(f"SELECT avg(power_watts) FROM {config.SQLITE_TABLE_SENSOR} WHERE timestamp LIKE ? AND device_type = 'Nature Remo E Lite'", (f"{today_str}%",))
        row = cursor.fetchone()
        avg_w = row[0] if row and row[0] is not None else 0
        data['electricity'] = { "estimated_daily_bill_yen": int((avg_w*24/1000)*31), "avg_watts": int(avg_w), "status": "Generating" if avg_w < 0 else "Consuming" }
        
        # 4. 車
        cursor.execute(f"SELECT count(*) FROM {config.SQLITE_TABLE_CAR} WHERE timestamp LIKE ? AND action='LEAVE'", (f"{today_str}%",))
        data['car_outing_count'] = cursor.fetchone()[0]
        
        # 5. 子供
        cursor.execute(f"SELECT child_name, condition FROM {config.SQLITE_TABLE_CHILD} WHERE timestamp LIKE ?", (f"{today_str}%",))
        data['children_health'] = [{ "child": r["child_name"], "condition": r["condition"] } for r in cursor.fetchall()]

    # 6. 天気 (APIコール)
    try:
        data['weather_report'] = WeatherService().get_weather_report()
    except Exception as e:
        logger.error(f"天気情報取得失敗: {e}")
        data['weather_report'] = "（天気情報の取得に失敗しました）"

    return data

def get_time_context(hour):
    """時間帯ごとのコンテキスト設定"""
    if 5 <= hour < 11:
        return {
            "context": "朝です。今日一日のスタートに向けた、明るく爽やかなメッセージにしてください。",
            "greeting": "おはようございます",
            "closing": "それでは、素敵な一日を！行ってらっしゃい👋"
        }
    elif 11 <= hour < 17:
        return {
            "context": "昼です。家事や育児の合間の休憩を促し、午後も無理しないよう伝える労いのメッセージにしてください。",
            "greeting": "こんにちは、お疲れ様です",
            "closing": "お昼ご飯は済みましたか？午後もほどほどに頑張りましょう🍵"
        }
    else:
        return {
            "context": "夜です。今日一日の労をねぎらい、ゆっくり休むよう伝える温かいメッセージにしてください。",
            "greeting": "今日もお疲れ様でした",
            "closing": "今日の夕食はどうしますか？ゆっくり休んでくださいね🌙"
        }

def build_system_prompt(data):
    mom_name = getattr(config, "MOM_NAME", "奥様")
    
    # 時間帯によるコンテキスト切り替え
    hour = datetime.now(pytz.timezone('Asia/Tokyo')).hour
    time_ctx = get_time_context(hour)

    return f"""
    あなたは「優秀で気が利く、少しユーモアのある執事」です。
    主人の代わりに、妻の{mom_name}さんへ「現在の家の状況」をレポートします。
    
    【現在の状況】
    {time_ctx['context']}
    挨拶は「{time_ctx['greeting']}」から始めてください。

    【家族構成】
    {get_family_profile()}

    【データ (JSON)】
    {json.dumps(data, ensure_ascii=False)}

    【作成ルール】
    1. トーン: 丁寧語だが親しみやすく。絵文字を使用。
    2. 内容優先度:
       - **天気情報** (データ内の 'weather_report' を参照し、洗濯や外出時の服装アドバイスを一言添える)
       - 子供のこと (記録があれば必ず触れる)
       - 実家の様子 (反応があれば安心させる)
    3. 締め: 「{time_ctx['closing']}」のようなニュアンスで。
    4. 長さ: スマホで読みやすいよう、200〜300文字程度。改行は適度に入れて読みやすく。
    """

def generate_report(model, data):
    print("🧠 [AI Thinking]...")
    prompt = build_system_prompt(data)
    response = model.generate_content(prompt)
    return response.text.strip()

def save_report_to_db(message):
    return common.save_log_generic(
        config.SQLITE_TABLE_AI_REPORT, 
        ["message", "timestamp"], 
        (message, common.get_now_iso())
    )

def send_notification(message, target):
    print(f"📤 [Sending] -> {target}")
    actions = [("🏠 自炊", "食事カテゴリ_自炊"), ("🍜 外食", "食事カテゴリ_外食"), ("🍱 その他", "食事カテゴリ_その他"), ("スキップ", "食事_スキップ")]
    items = [{"type": "action", "action": {"type": "message", "label": l, "text": t}} for l, t in actions]
    msg_payload = {"type": "text", "text": message, "quickReply": {"items": items}}
    
    targets = ['line', 'discord'] if target == 'both' else [target]
    success = False
    for t in targets:
        if common.send_push(config.LINE_USER_ID, [msg_payload], target=t, channel="report"):
            print(f"   ✅ {t}: OK")
            success = True
    return success

def main():
    print(f"\n🚀 --- AI Reporter: {datetime.now().strftime('%H:%M:%S')} ---")
    args = parse_arguments()
    try:
        model = setup_gemini()
        data = fetch_daily_data()
        text = generate_report(model, data)
        print(f"\n📝 Report:\n{text}\n")
        
        save_report_to_db(text)
        if send_notification(text, args.target): print("🎉 Done")
        else: sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}")
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": f"😰 AI Error: {e}"}], target="discord", channel="error")
        sys.exit(1)

if __name__ == "__main__":
    main()