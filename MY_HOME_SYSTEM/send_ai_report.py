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

# 各種サービスのインポート
from weather_service import WeatherService
from news_service import NewsService
from menu_service import MenuService

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

# 元の実装から一切変更しない
def setup_gemini():
    if not config.GEMINI_API_KEY:
        logger.error("❌ Gemini API Keyなし")
        sys.exit(1)
    genai.configure(api_key=config.GEMINI_API_KEY)
    candidates = ["gemini-1.5-flash", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-pro"]
    try:
        models = [m.name.replace("models/", "") for m in genai.list_models()]
        for c in candidates:
            if c in models: return genai.GenerativeModel(c)
        return genai.GenerativeModel("gemini-1.5-flash")
    except: return genai.GenerativeModel("gemini-1.5-flash")

def fetch_daily_data():
    """センサー、DB、外部APIから日次データを収集する"""
    data = {}
    today_str = common.get_today_date_str()
    
    # 現在時刻（JST）
    jst = pytz.timezone('Asia/Tokyo')
    now = datetime.now(jst)
    current_hour = now.hour
    weekday = now.weekday() # 0:月, 4:金, 6:日
    
    # 金曜日の夜(17時以降)かどうか判定 (機能追加部分)
    data['is_friday_night'] = (weekday == 4 and current_hour >= 17)
    data['current_month'] = now.month
    
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
        data['news_topics'] = NewsService().get_top_news(limit=5)
    except Exception as e:
        logger.error(f"ニュース取得失敗: {e}")
        data['news_topics'] = []

    # 8. 晩御飯の提案 (お昼の時間帯 11:00-13:59 のみ実行)
    if 11 <= current_hour < 14:
        print("🍳 [Data Fetching] Menu Suggestion...")
        try:
            ms = MenuService()
            data['menu_suggestion_context'] = {
                "recent_menus": ms.get_recent_menus(days=5), 
                "special_day": ms.get_special_day_info()
            }
        except Exception as e:
            logger.error(f"メニュー情報取得失敗: {e}")

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

    # --- メニュー提案セクション ---
    menu_prompt_section = ""
    if 'menu_suggestion_context' in data:
        ctx = data['menu_suggestion_context']
        special_day = ctx.get('special_day')
        recent_menus = ctx.get('recent_menus', [])
        
        recent_history_str = "\n".join(recent_menus) if recent_menus else "(履歴なし)"
        special_msg = f"※ 今日は「{special_day}」です！" if special_day else ""
        
        menu_prompt_section = f"""
        【晩御飯の献立提案 (重要)】
        お昼の連絡なので、主婦の味方として「今夜の献立」を3つ提案してください。
        [提案の条件]
        1. **「主婦が気軽に作れる」** 手間のかかりすぎないもの。
        2. 直近の履歴 ({recent_history_str}) と被らないもの。
        3. {special_msg}
        """

    # --- 週末イベント提案セクション (機能追加部分) ---
    event_prompt_section = ""
    if data.get('is_friday_night'):
        month = data.get('current_month', 12)
        event_prompt_section = f"""
        【週末お出かけ提案 (重要)】
        今日は金曜日の夜です。明日の土日に家族（5歳と2歳の子供連れ）で楽しめそうな、
        「兵庫・大阪・奈良」エリアの定番スポットや、{month}月の季節に合った過ごし方を1つ提案してください。
        （例: 寒いので屋内の○○、イルミネーションが見える○○、など）
        ※Web検索は使用せず、あなたの知識の中からおすすめを提案してください。
        """

    # --- プロンプトの組み立て ---
    return f"""
    あなたは「優秀で気が利く、少しユーモアのある執事」です。名前はセバスチャンです。
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
    2. **構成**:
       - **挨拶 & 天気**: 天気データ('weather_report')を見て、服装や傘の一言アドバイス。
       - **ニュース**: 'news_topics' から3つ選んで紹介。
         **重要(変更)**: Discordのプレビューカードを非表示にし、かつリンクにするために、URLは必ず **`[タイトル](<URL>)`** の形式（URLを `<` と `>` で囲む）で記述してください。
       - **夕食の提案**: {menu_prompt_section if menu_prompt_section else "（この時間は提案不要）"}
       - **週末イベント**: {event_prompt_section if event_prompt_section else "（この時間は提案不要）"}
       - **家の状況**: 子供の記録があれば触れる。
    3. **締め**: 「{time_ctx['closing']}」のようなニュアンスで。
    4. **長さ**: 全体で **500文字前後**。改行や絵文字を使って読みやすく整形してください。
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
        # エラー時はDiscordのErrorチャンネルに通知
        common.send_push(
            config.LINE_USER_ID, 
            [{"type": "text", "text": f"😰 AI Reporter Error: {e}"}], 
            target="discord", 
            channel="error"
        )
        sys.exit(1)

if __name__ == "__main__":
    main()