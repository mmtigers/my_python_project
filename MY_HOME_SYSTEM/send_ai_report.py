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

from weather_service import WeatherService
from news_service import NewsService

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
    # モデルの選択ロジック
    candidates = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash", "gemini-pro"]
    try:
        models = [m.name.replace("models/", "") for m in genai.list_models()]
        for c in candidates:
            if c in models: return genai.GenerativeModel(c)
        return genai.GenerativeModel("gemini-1.5-flash")
    except: return genai.GenerativeModel("gemini-1.5-flash")

def fetch_daily_data():
    """各種データを収集する"""
    data = {}
    today_str = common.get_today_date_str()
    
    print("📊 [Data Fetching] DB & Sensors...")
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

    # 6. 天気
    print("🌤️ [Data Fetching] Weather...")
    try:
        data['weather_report'] = WeatherService().get_weather_report()
    except Exception as e:
        logger.error(f"天気情報取得失敗: {e}")
        data['weather_report'] = "（天気情報の取得に失敗しました）"

    # 7. ニュース
    print("📰 [Data Fetching] News...")
    try:
        # 最新5件を取得 (辞書リスト)
        data['news_topics'] = NewsService().get_top_news(limit=5)
    except Exception as e:
        logger.error(f"ニュース取得失敗: {e}")
        data['news_topics'] = []

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
    1. **役割**: 忙しい主婦の味方として、簡潔かつ温かい言葉を選んでください。
    2. **構成と内容**:
       - **挨拶 & 天気**: 天気データ('weather_report')を見て、服装のアドバイスを一言。
       - **今日のニュース**: 提供された 'news_topics' (タイトルとURLのリスト) から**3つ**を選び、紹介してください。
         ※ 重要: 各ニュースは「タイトル」の次の行に「URL」を記載する形式にしてください。
       - **家の状況**: 子供の体調('children_health')や実家('parents_home')の記録があれば必ず触れてください。
    3. **締め**: 「{time_ctx['closing']}」のようなニュアンスで。
    4. **長さ**: 情報をしっかり伝えるため、**500文字前後**で作成してください。改行や絵文字を使って視認性を高めてください。
    """

def generate_report(model, data):
    print("🧠 [AI Thinking] 生成中...")
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
            print(f"   ✅ {t}: 送信成功")
            success = True
    return success

def main():
    print(f"\n🚀 --- AI Reporter: {datetime.now().strftime('%H:%M:%S')} ---")
    args = parse_arguments()
    try:
        model = setup_gemini()
        data = fetch_daily_data()
        text = generate_report(model, data)
        print(f"\n📝 Generated Report:\n{'-'*30}\n{text}\n{'-'*30}\n")
        
        save_report_to_db(text)
        if send_notification(text, args.target): 
            print("🎉 All tasks completed successfully.")
        else: 
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Critical Error: {e}")
        traceback.print_exc()
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": f"😰 AI Reporter Error: {e}"}], target="discord", channel="error")
        sys.exit(1)

if __name__ == "__main__":
    main()