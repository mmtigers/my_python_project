# MY_HOME_SYSTEM/send_ai_report.py
import google.generativeai as genai
import json
import config
import common
import traceback
import argparse
import sqlite3
import sys
from datetime import datetime
import pytz
from PIL import Image
from typing import Dict, Any, List, Optional, Tuple

# 各種サービスのインポート
from weather_service import WeatherService
from news_service import NewsService
from menu_service import MenuService
import tools.camera_digest_service as camera_digest_service
from core import logger as core_logger # 規約に従いcoreからインポート

# ロガーの初期化 
logger = common.setup_logging("ai_report")

def get_family_profile() -> str:
    """
    家族構成のプロファイルを生成します。
    Configから読み込むことでハードコードを排除しています。

    Returns:
        str: 家族構成の説明テキスト
    """
    dad_name: str = getattr(config, "DAD_NAME", "旦那様")
    mom_name: str = getattr(config, "MOM_NAME", "奥様")
    children_names: List[str] = getattr(config, "CHILDREN_NAMES", [])
    children_info: str = ", ".join(children_names) if children_names else "お子様たち"
    
    return f"""
    - 夫: {dad_name} (仕事熱心)
    - 妻: {mom_name} (専業主婦, 家事育児に奮闘中)
    - 子供: {children_info}
    - 住まい: {getattr(config, "HOME_LOCATION", "自宅")}
    - 実家: {getattr(config, "PARENTS_LOCATION", "実家")}
    """

def parse_arguments() -> argparse.Namespace:
    """コマンドライン引数を解析します。"""
    parser = argparse.ArgumentParser(description='AI日報送信スクリプト')
    parser.add_argument('--target', type=str, default='discord', choices=['line', 'discord', 'both'], help='通知先')
    return parser.parse_args()

def setup_gemini() -> genai.GenerativeModel:
    """
    Gemini APIクライアントを初期化します。
    APIキーが存在しない場合はシステムを終了させます。

    Returns:
        genai.GenerativeModel: 初期化されたモデルインスタンス
    """
    if not config.GEMINI_API_KEY:
        logger.critical("❌ Gemini API Key not found in configuration.")
        sys.exit(1)
        
    genai.configure(api_key=config.GEMINI_API_KEY)
    
    # モデルのフォールバックロジック
    candidates = ["gemini-1.5-flash", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-pro"]
    try:
        models = [m.name.replace("models/", "") for m in genai.list_models()]
        for c in candidates:
            if c in models:
                logger.debug(f"Selected Gemini Model: {c}")
                return genai.GenerativeModel(c)
        logger.warning("Preferred models not found. Fallback to gemini-1.5-flash.")
        return genai.GenerativeModel("gemini-1.5-flash")
    except Exception as e:
        logger.error(f"Failed to list models: {e}. Fallback to default.")
        return genai.GenerativeModel("gemini-1.5-flash")

def fetch_daily_data() -> Dict[str, Any]:
    """
    センサー、DB、外部APIから日次データを収集します。
    Fail-Soft設計: 個別のデータ取得に失敗しても、可能な限り処理を継続します。 

    Returns:
        Dict[str, Any]: AIプロンプト生成用のデータ辞書
    """
    data: Dict[str, Any] = {}
    today_str = common.get_today_date_str()
    
    # 現在時刻（JST）
    jst = pytz.timezone('Asia/Tokyo')
    now = datetime.now(jst)
    current_hour = now.hour
    weekday = now.weekday() # 0:Mon, 4:Fri, 6:Sun
    
    data['is_friday_night'] = (weekday == 4 and current_hour >= 17)
    data['current_month'] = now.month
    
    logger.info("📊 [Data Fetching] Starting data collection...")

    # --- DB & Sensors (Critical Section: DB Connection) ---
    try:
        with common.get_db_cursor() as cursor:
            if not cursor:
                raise ConnectionError("Database cursor is None")
            
            # 1. Environment (Itami)
            try:
                itami_ids = [d['id'] for d in config.MONITOR_DEVICES if d.get('location') == '伊丹']
                cursor.execute(
                    f"SELECT device_id, device_name, avg(temperature_celsius) as t, avg(humidity_percent) as h "
                    f"FROM {config.SQLITE_TABLE_SENSOR} "
                    f"WHERE timestamp LIKE ? AND device_type LIKE '%Meter%' GROUP BY device_id", 
                    (f"{today_str}%",)
                )
                data['environment'] = [
                    { "place": r["device_name"], "temp": round(r["t"],1), "humidity": round(r["h"],1) } 
                    for r in cursor.fetchall() 
                    if r["device_id"] in itami_ids
                ]
            except Exception as e:
                logger.warning(f"⚠️ Failed to fetch environment data: {e}")
                data['environment'] = []

            # 2. Parents Home (Optional)
            try:
                target_loc = getattr(config, "PARENTS_LOCATION", "高砂")
                taka_ids = [d["id"] for d in config.MONITOR_DEVICES if d.get("location") == target_loc and "Contact" in d.get("type", "")]
                if taka_ids:
                    placeholders = ",".join(["?"] * len(taka_ids))
                    cursor.execute(
                        f"SELECT device_name, COUNT(*) FROM {config.SQLITE_TABLE_SENSOR} "
                        f"WHERE timestamp LIKE ? AND device_id IN ({placeholders}) "
                        f"AND contact_state IN ('open', 'detected') GROUP BY device_id", 
                        (f"{today_str}%", *taka_ids)
                    )
                    data['parents_home'] = {r["device_name"]: r[1] for r in cursor.fetchall()}
            except Exception as e:
                logger.warning(f"⚠️ Failed to fetch parents home data: {e}")
                data['parents_home'] = {}

            # 3. Electricity (Optional)
            try:
                cursor.execute(
                    f"SELECT avg(power_watts) FROM {config.SQLITE_TABLE_SENSOR} "
                    f"WHERE timestamp LIKE ? AND device_type = 'Nature Remo E Lite'", 
                    (f"{today_str}%",)
                )
                row = cursor.fetchone()
                avg_w = row[0] if row and row[0] is not None else 0
                data['electricity'] = { 
                    "estimated_daily_bill_yen": int((avg_w*24/1000)*31), 
                    "avg_watts": int(avg_w), 
                    "status": "Generating" if avg_w < 0 else "Consuming" 
                }
            except Exception as e:
                logger.warning(f"⚠️ Failed to fetch electricity data: {e}")
                data['electricity'] = {"status": "Unknown"}

            # 4. Car (Optional)
            try:
                cursor.execute(
                    f"SELECT count(*) FROM {config.SQLITE_TABLE_CAR} WHERE timestamp LIKE ? AND action='LEAVE'", 
                    (f"{today_str}%",)
                )
                result = cursor.fetchone()
                data['car_outing_count'] = result[0] if result else 0
            except Exception as e:
                logger.warning(f"⚠️ Failed to fetch car data: {e}")
                data['car_outing_count'] = 0

            # 5. Children Health (Optional)
            try:
                cursor.execute(
                    f"SELECT child_name, condition FROM {config.SQLITE_TABLE_CHILD} WHERE timestamp LIKE ?", 
                    (f"{today_str}%",)
                )
                data['children_health'] = [{ "child": r["child_name"], "condition": r["condition"] } for r in cursor.fetchall()]
            except Exception as e:
                logger.warning(f"⚠️ Failed to fetch children health: {e}")
                data['children_health'] = []

            # 10. Family Quest (Optional)
            try:
                cursor.execute("""
                    SELECT u.name, t.title, t.points
                    FROM quest_status s
                    JOIN quest_tasks t ON s.task_id = t.id
                    JOIN quest_users u ON t.target_user_id = u.rowid
                    WHERE s.date = ? AND s.is_completed = 1
                """, (today_str,))
                data['quest_achievements'] = [
                    {"user": r["name"], "title": r["title"], "points": r["points"]} 
                    for r in cursor.fetchall()
                ]
            except sqlite3.OperationalError as e:
                logger.warning(f"⚠️ Quest data skipped (Schema mismatch?): {e}")
                data['quest_achievements'] = []
            except Exception as e:
                logger.warning(f"⚠️ Failed to fetch quest data: {e}")
                data['quest_achievements'] = []

    except Exception as e:
        logger.error(f"🔥 Critical DB Error during data fetch: {e}")
        # DB接続自体が失敗しても、天気やニュースだけでレポートを作るために続行する
        traceback.print_exc()

    # --- External APIs (Fail-Soft) ---

    # 6. Weather
    try:
        logger.info("🌤️ [Data Fetching] Weather...")
        data['weather_report'] = WeatherService().get_weather_report_text()
    except Exception as e:
        logger.warning(f"⚠️ Weather API failed: {e}")
        data['weather_report'] = "（天気情報の取得に失敗しました）"

    # 7. News
    try:
        logger.info("📰 [Data Fetching] News...")
        data['news_topics'] = NewsService().get_top_news(limit=5)
    except Exception as e:
        logger.warning(f"⚠️ News API failed: {e}")
        data['news_topics'] = []

    # 8. Menu Suggestion (Time restricted)
    if 11 <= current_hour < 14:
        try:
            logger.info("🍳 [Data Fetching] Menu Suggestion...")
            ms = MenuService()
            data['menu_suggestion_context'] = {
                "recent_menus": ms.get_recent_menus(days=5), 
                "special_day": ms.get_special_day_info()
            }
        except Exception as e:
            logger.warning(f"⚠️ Menu Service failed: {e}")

    # 9. Camera Images
    try:
        logger.info("📷 [Data Fetching] Camera Images...")
        data['camera_images_paths'] = camera_digest_service.get_todays_highlight_images(limit=8)
    except Exception as e:
        logger.warning(f"⚠️ Camera digest failed: {e}")
        data['camera_images_paths'] = []

    return data

def get_time_context(hour: int) -> Dict[str, str]:
    """
    時間帯ごとのコンテキスト設定を返します。

    Args:
        hour (int): 現在の時 (0-23)
    Returns:
        Dict[str, str]: 挨拶文やコンテキスト情報
    """
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

def build_system_prompt(data: Dict[str, Any]) -> str:
    """
    Geminiへのシステムプロンプトを構築します。
    """
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

    # --- 週末イベント提案 ---
    event_prompt_section = ""
    if data.get('is_friday_night'):
        month = data.get('current_month', 12)
        event_prompt_section = f"""
        【週末お出かけ提案 (重要)】
        今日は金曜日の夜です。明日の土日に家族（5歳と2歳の子供連れ）で楽しめそうな、
        「兵庫・大阪・奈良」エリアの定番スポットや、{month}月の季節に合った過ごし方を1つ提案してください。
        """

    # --- クエスト成果 ---
    quest_prompt_section = ""
    achievements = data.get('quest_achievements', [])
    if achievements:
        user_quests: Dict[str, List[str]] = {}
        total_points = 0
        for item in achievements:
            name = item['user']
            if name not in user_quests: user_quests[name] = []
            user_quests[name].append(item['title'])
            total_points += item.get('points', 0)
        
        lines = [f"- {name}: {', '.join(titles)}" for name, titles in user_quests.items()]
        quest_summary = "\n".join(lines)
        
        quest_prompt_section = f"""
        【本日のお手伝い・クエスト成果 (重要)】
        合計 {total_points}pt 獲得。具体的に褒めてください。
        [達成リスト]
        {quest_summary}
        """

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
       - **挨拶 & 天気**: 天気データを見て、服装や傘の一言アドバイス。
       - **ニュース**: 'news_topics' から3つ選んで紹介。URLは `[タイトル](<URL>)` 形式必須。
         **重要(変更)**: Discordのプレビューカードを非表示にし、かつリンクにするために、URLは必ず **`[タイトル](<URL>)`** の形式（URLを `<` と `>` で囲む）で記述してください。
       - **夕食の提案**: {menu_prompt_section if menu_prompt_section else "（提案不要）"}
       - **週末イベント**: {event_prompt_section if event_prompt_section else "（提案不要）"}
       - **お手伝い成果**: {quest_prompt_section if quest_prompt_section else "（特になし）"}
       - **家の状況**: 子供の記録があれば触れる。高砂や実家の状況は触れない。
    3. **締め**: 「{time_ctx['closing']}」のようなニュアンスで。
    4. **長さ**: 500文字前後。
    """

def generate_report(model: genai.GenerativeModel, data: Dict[str, Any]) -> str:
    """Geminiを使用してレポートテキストを生成します。"""
    logger.info("🧠 [AI Thinking] Generating report...")
    
    prompt = build_system_prompt(data)
    content_parts: List[Any] = [prompt]
    
    image_paths = data.get('camera_images_paths', [])
    images_loaded: List[Image.Image] = []
    
    if image_paths:
        logger.info(f"   🖼️ Attaching {len(image_paths)} images...")
        for path in image_paths:
            try:
                img = Image.open(path)
                images_loaded.append(img)
                content_parts.append(img)
            except Exception as e:
                logger.error(f"Failed to load image ({path}): {e}")

    if images_loaded:
        content_parts[0] += "\n\n【追加指示】添付画像は防犯カメラ映像です。異常がないか「📷 防犯カメラハイライト」として報告してください。"

    try:
        response = model.generate_content(content_parts)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Gemini generation failed: {e}")
        raise
    finally:
        # リソース管理: 明示的なclose [cite: 423]
        for img in images_loaded:
            img.close()

def save_report_to_db(message: str) -> bool:
    """生成されたレポートをDBに保存します。"""
    try:
        common.save_log_generic(
            config.SQLITE_TABLE_AI_REPORT, 
            ["message", "timestamp"], 
            (message, common.get_now_iso())
        )
        return True
    except Exception as e:
        logger.error(f"Failed to save report to DB: {e}")
        return False

def send_notification(message: str, target: str) -> bool:
    """
    LINE/Discordへ通知を送信します。
    """
    logger.info(f"📤 [Sending] -> {target}")
    actions = [("🏠 自炊", "食事カテゴリ_自炊"), ("🍜 外食", "食事カテゴリ_外食"), ("🍱 その他", "食事カテゴリ_その他"), ("スキップ", "食事_スキップ")]
    items = [{"type": "action", "action": {"type": "message", "label": l, "text": t}} for l, t in actions]
    msg_payload = {"type": "text", "text": message, "quickReply": {"items": items}}
    
    targets = ['line', 'discord'] if target == 'both' else [target]
    success_count = 0
    
    for t in targets:
        try:
            if common.send_push(config.LINE_USER_ID, [msg_payload], target=t, channel="report"):
                logger.info(f"   ✅ {t}: Sent successfully")
                success_count += 1
            else:
                logger.error(f"   ❌ {t}: Send failed")
        except Exception as e:
            logger.error(f"   ❌ {t}: Exception during send: {e}")
            
    return success_count > 0

def main():
    logger.info(f"🚀 --- AI Reporter Started: {datetime.now().strftime('%H:%M:%S')} ---")
    args = parse_arguments()
    
    try:
        model = setup_gemini()
        data = fetch_daily_data()
        
        # 少なくともデータ取得の試行が終わった後にレポート生成へ
        text = generate_report(model, data)
        logger.debug(f"📝 Generated Report Preview:\n{text[:100]}...")
        
        save_report_to_db(text)
        
        if send_notification(text, args.target): 
            logger.info("🎉 All tasks completed successfully.")
        else: 
            logger.error("❌ Notification failed.")
            sys.exit(1)
            
    except Exception as e:
        logger.critical(f"🔥 Critical System Error: {e}")
        logger.error(traceback.format_exc())
        
        # エラー通知
        common.send_push(
            config.LINE_USER_ID, 
            [{"type": "text", "text": f"😰 AI Reporter Error: {e}"}], 
            target="discord", 
            channel="error"
        )
        sys.exit(1)

if __name__ == "__main__":
    main()