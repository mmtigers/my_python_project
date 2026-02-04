# MY_HOME_SYSTEM/services/ai_service.py
import google.generativeai as genai
from google.generativeai.types import FunctionDeclaration, Tool
import json
import traceback
import sqlite3
import re
import common
import config

# ロガー設定
logger = common.setup_logging("ai_service")

# Gemini初期化
if config.GEMINI_API_KEY:
    genai.configure(api_key=config.GEMINI_API_KEY)
else:
    logger.warning("⚠️ GEMINI_API_KEYが設定されていません。AI機能は無効です。")

# ==========================================
# 0. データベーススキーマ定義
# ==========================================
DB_SCHEMA_INFO = f"""
あなたは以下のSQLiteテーブルを持つホームシステムのデータベースにアクセスできます。
ユーザーの質問に答えるために、適切なSQLクエリを作成してデータを検索してください。

【テーブル定義】
1. {config.SQLITE_TABLE_CHILD} (子供の体調)
   - Columns: timestamp (日時), child_name (名前), condition (症状・様子)
2. {config.SQLITE_TABLE_SHOPPING} (買い物履歴)
   - Columns: order_date (注文日), platform (Amazon/Rakuten/LINE入力), item_name (商品名), price (金額)
3. {config.SQLITE_TABLE_FOOD} (食事記録)
   - Columns: timestamp (日時), menu_category (メニュー内容: '自炊: カレー' 等), meal_time_category (Dinner等)
4. {config.SQLITE_TABLE_SENSOR} (センサー・電力データ)
   - Columns: timestamp, device_name, device_type, power_watts, temperature_celsius, humidity_percent
5. {config.SQLITE_TABLE_CAR} (車の移動)
   - Columns: timestamp, action (LEAVE/RETURN)
6. {config.SQLITE_TABLE_DEFECATION} (排便記録)
   - Columns: timestamp, user_name, condition, note
"""

# ==========================================
# 1. ツール定義 (Interface)
# ==========================================

def declare_child_health(child_name: str, condition: str, is_emergency: bool = False):
    """子供の体調や怪我、様子を記録する。"""
    pass

def declare_shopping(item_name: str, price: int, date_str: str = None):
    """買い物や支出を記録する。"""
    pass

def declare_defecation(condition: str, note: str = ""):
    """排便やトイレ、お腹の調子を記録する。"""
    pass

def search_database(sql_query: str):
    """データベースから情報を検索する。SELECT文のみ許可。"""
    pass

def get_health_logs(child_name: str = None, days: int = 7):
    """子供の体調記録や排便記録を確認する。"""
    args = {"child_name": child_name, "days": days}
    return execute_get_health_logs(args)

def get_expenditure_logs(item_keyword: str = None, platform: str = None, days: int = 30):
    """過去の買い物履歴や支出を検索する。"""
    args = {"item_keyword": item_keyword, "platform": platform, "days": days}
    return execute_get_expenditure_logs(args)

my_tools = [declare_child_health, declare_shopping, declare_defecation, search_database, get_health_logs, get_expenditure_logs]

# ==========================================
# 2. 実行ロジック
# ==========================================

def execute_child_health(args, user_id, user_name):
    child_name = args.get("child_name", "子供")
    condition = args.get("condition", "記録なし")
    
    common.save_log_generic(config.SQLITE_TABLE_CHILD,
        ["user_id", "user_name", "child_name", "condition", "timestamp"],
        (user_id, user_name, child_name, condition, common.get_now_iso())
    )
    
    msg = f"📝 {child_name}ちゃんの様子を記録しました:「{condition}」"
    if args.get("is_emergency"):
        msg += "\n無理せず、お大事にしてくださいね😢"
        common.send_push(config.LINE_USER_ID, [{"type": "text", "text": f"🚨 {child_name}: {condition}"}], target="discord")
    return msg

def execute_shopping(args, user_id, user_name):
    item = args.get("item_name")
    try: price = int(args.get("price", 0))
    except: price = 0
    date_str = args.get("date_str") or common.get_today_date_str()
    import time
    unique_id = f"LINE_MANUAL_{int(time.time())}_{price}"
    
    common.save_log_generic(config.SQLITE_TABLE_SHOPPING,
        ["platform", "order_date", "item_name", "price", "email_id", "timestamp"],
        ("LINE入力", date_str, item, price, unique_id, common.get_now_iso())
    )
    return f"💰 家計簿につけました！\n{date_str}: {item} ({price}円)"

def execute_defecation(args, user_id, user_name):
    condition = args.get("condition")
    note = args.get("note", "")
    common.save_log_generic(config.SQLITE_TABLE_DEFECATION,
        ["user_id", "user_name", "record_type", "condition", "note", "timestamp"],
        (user_id, user_name, "排便", condition, note, common.get_now_iso())
    )
    return f"🚽 お腹の記録をしました。\n状態: {condition}"

def execute_search_database(args):
    query = args.get("sql_query", "")
    if not re.match(r"^\s*SELECT", query, re.IGNORECASE):
        return "❌ エラー: データ検索以外の操作は許可されていません。"
    try:
        conn = sqlite3.connect(f"file:{config.SQLITE_DB_PATH}?mode=ro", uri=True)
        cursor = conn.cursor()
        logger.info(f"🔍 Executing SQL: {query}")
        cursor.execute(query)
        rows = cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        conn.close()
        if not rows: return "該当するデータは見つかりませんでした。"
        return json.dumps([dict(zip(columns, row)) for row in rows], ensure_ascii=False, default=str)
    except Exception as e:
        logger.error(f"SQL Error: {e}")
        return f"検索中にエラーが発生しました: {str(e)}"

def execute_get_health_logs(args):
    child_name = args.get("child_name")
    days = args.get("days", 7)
    query = f"""
        SELECT timestamp, child_name as target, condition, '体調' as type 
        FROM {config.SQLITE_TABLE_CHILD} 
        WHERE timestamp > datetime('now', '-? days')
        UNION ALL
        SELECT timestamp, user_name as target, condition, '排便' as type 
        FROM {config.SQLITE_TABLE_DEFECATION} 
        WHERE timestamp > datetime('now', '-? days')
    """
    params = [days, days]
    if child_name:
        query = f"SELECT * FROM ({query}) WHERE target LIKE ?"
        params.append(f"%{child_name}%")
    return common.execute_read_query(query, tuple(params))

def execute_get_expenditure_logs(args):
    keyword = args.get("item_keyword")
    platform = args.get("platform")
    days = args.get("days", 30)
    query = f"SELECT order_date, platform, item_name, price FROM {config.SQLITE_TABLE_SHOPPING} WHERE order_date > datetime('now', '-? days')"
    params = [days]
    if keyword:
        query += " AND item_name LIKE ?"
        params.append(f"%{keyword}%")
    if platform:
        query += " AND platform = ?"
        params.append(platform)
    query += " ORDER BY order_date DESC"
    return common.execute_read_query(query, tuple(params))

# ==========================================
# 3. メイン処理 (Gemini呼び出し)
# ==========================================

def analyze_text_and_execute(text: str, user_id: str, user_name: str) -> str:
    """Geminiで解析しツール実行または応答を返す"""
    if not config.GEMINI_API_KEY: return None 
    try:
        model = genai.GenerativeModel('gemini-2.5-flash', tools=my_tools)
        prompt = f"""
        ユーザー名: {user_name}
        現在日時: {common.get_now_iso()}
        あなたは家庭用アシスタント「セバスチャン」です。
        ユーザーの意図を理解し、記録ツールまたは情報検索ツール(search_database)を呼び出すか、親しみやすく返答してください。
        {DB_SCHEMA_INFO}
        メッセージ: {text}
        """
        chat = model.start_chat(enable_automatic_function_calling=True)
        response = chat.send_message(prompt)
        if response.text: return response.text.strip()
    except Exception as e:
        logger.error(f"AI解析エラー: {e}")
        logger.error(traceback.format_exc())
        return "申し訳ありません、処理中にエラーが発生しました🙇"
    return None