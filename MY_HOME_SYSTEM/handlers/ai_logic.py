import google.generativeai as genai
from google.generativeai.types import FunctionDeclaration, Tool
import json
import datetime
import traceback
import sqlite3
import re
import common
import config

# ロガー設定
logger = common.setup_logging("ai_logic")

# Gemini初期化
if config.GEMINI_API_KEY:
    genai.configure(api_key=config.GEMINI_API_KEY)
else:
    logger.warning("⚠️ GEMINI_API_KEYが設定されていません。AI機能は無効です。")

# ==========================================
# 0. データベーススキーマ定義 (AI用コンテキスト)
# ==========================================
DB_SCHEMA_INFO = f"""
あなたは以下のSQLiteテーブルを持つホームシステムのデータベースにアクセスできます。
ユーザーの質問に答えるために、適切なSQLクエリを作成してデータを検索してください。

【テーブル定義】
1. {config.SQLITE_TABLE_CHILD} (子供の体調)
   - Columns: timestamp (日時), child_name (名前), condition (症状・様子)
   - 用途: 「子供の熱はいつ？」「最近の体調は？」などの質問に使用。

2. {config.SQLITE_TABLE_SHOPPING} (買い物履歴)
   - Columns: order_date (注文日), platform (Amazon/Rakuten/LINE入力), item_name (商品名), price (金額)
   - 用途: 「最近何買った？」「今月のAmazonの利用額は？」などの質問に使用。

3. {config.SQLITE_TABLE_FOOD} (食事記録)
   - Columns: timestamp (日時), menu_category (メニュー内容: '自炊: カレー' 等), meal_time_category (Dinner等)
   - 用途: 「先週の夕食は何だった？」「外食の頻度は？」などの質問に使用。

4. {config.SQLITE_TABLE_SENSOR} (センサー・電力データ)
   - Columns: timestamp, device_name, device_type, power_watts, temperature_celsius, humidity_percent
   - 用途: 「今の室温は？」「電気代（電力）の推移は？」などの質問に使用。
   - 注意: 電気代計算には 'Nature Remo E Lite' の power_watts を使用。

5. {config.SQLITE_TABLE_CAR} (車の移動)
   - Columns: timestamp, action (LEAVE/RETURN)
   - 用途: 「いつ外出した？」「車は今ある？」などの質問に使用。

6. {config.SQLITE_TABLE_DEFECATION} (排便記録)
   - Columns: timestamp, user_name, condition, note
   - 用途: 「お腹の調子」「うんちいつ出た？」などの質問に使用。

【SQL作成ルール】
- 日付比較は `timestamp` カラム (ISO8601形式: YYYY-MM-DDTHH:MM:SS) または `date` カラムを使用。
- 直近のデータを検索する場合は `ORDER BY timestamp DESC LIMIT N` を活用する。
- 曖昧検索には `LIKE '%キーワード%'` を使用する。
"""

# ==========================================
# 1. ツール定義 (関数宣言)
# ==========================================

# --- 既存の記録用ツール ---

def declare_child_health(child_name: str, condition: str, is_emergency: bool = False):
    """子供の体調や怪我、様子を記録する。"""
    pass

def declare_shopping(item_name: str, price: int, date_str: str = None):
    """買い物や支出を記録する。"""
    pass

def declare_defecation(condition: str, note: str = ""):
    """排便やトイレ、お腹の調子を記録する。"""
    pass

# --- 新規追加: 検索用ツール ---

def search_database(sql_query: str):
    """データベースから情報を検索する。ユーザーの質問に答えるために必要なデータを取得するSQLを実行する。
    
    Args:
        sql_query: 実行するSQLite形式のSELECTクエリ (例: "SELECT * FROM child_health_records ORDER BY timestamp DESC LIMIT 5")
    """
    pass

def get_health_logs(child_name: str = None, days: int = 7):
    """
    子供の体調記録や排便記録を確認します。
    Args:
        child_name: 子供の名前（智矢、涼花、パパ、ママなど）。指定がない場合は全員。
        days: 過去何日分を遡るか（デフォルト7）。
    """
    # 実行ロジックへ委譲
    args = {"child_name": child_name, "days": days}
    return execute_get_health_logs(args)

# 実行用ロジックが先に定義されている必要があるため、ツールの下にある実行ロジックを参照できるようにする
# Pythonでは関数内で呼ぶ分には定義順序は問わないが、
# ここでは execute_get_expenditure_logs がまだ定義されていないため、
# execute_get_expenditure_logs の定義はこの後にある。
# よって、my_tools の定義をファイルの最後に移動するか、
# execute_ 関数群を先に書くのが正しいが、今回は安全に「ラッパー関数」を定義して
# execute_get_expenditure_logs を呼び出す形にする。

def execute_get_expenditure_logs(args):
    """買い物履歴を安全に検索"""
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

# ▼▼▼ 追加した関数 ▼▼▼
def get_expenditure_logs(item_keyword: str = None, platform: str = None, days: int = 30):
    """
    過去の買い物履歴や支出を検索します。
    Args:
        item_keyword: 商品名の一部（例: "お茶", "オムツ"）。
        platform: 購入先（Amazon, Rakuten, LINE入力）。指定なしなら全て。
        days: 検索対象の日数（デフォルト30日）。
    """
    args = {"item_keyword": item_keyword, "platform": platform, "days": days}
    return execute_get_expenditure_logs(args)
# ▲▲▲ 追加終了 ▲▲▲

# ツールセット登録
my_tools = [declare_child_health, declare_shopping, declare_defecation, get_health_logs, get_expenditure_logs]


# ==========================================
# 2. 実行ロジック
# ==========================================

def execute_child_health(args, user_id, user_name):
    """子供の体調をDBに保存"""
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
    """買い物をDBに保存"""
    item = args.get("item_name")
    try:
        price = int(args.get("price", 0))
    except (ValueError, TypeError):
        price = 0

    date_str = args.get("date_str")
    if not date_str:
        date_str = common.get_today_date_str()
    
    import time
    unique_id = f"LINE_MANUAL_{int(time.time())}_{price}"
    
    common.save_log_generic(config.SQLITE_TABLE_SHOPPING,
        ["platform", "order_date", "item_name", "price", "email_id", "timestamp"],
        ("LINE入力", date_str, item, price, unique_id, common.get_now_iso())
    )
    
    return f"💰 家計簿につけました！\n{date_str}: {item} ({price}円)"

def execute_defecation(args, user_id, user_name):
    """排便ログをDBに保存"""
    condition = args.get("condition")
    note = args.get("note", "")
    
    common.save_log_generic(config.SQLITE_TABLE_DEFECATION,
        ["user_id", "user_name", "record_type", "condition", "note", "timestamp"],
        (user_id, user_name, "排便", condition, note, common.get_now_iso())
    )
    
    return f"🚽 お腹の記録をしました。\n状態: {condition}"

def execute_search_database(args):
    """読み取り専用でSQLを実行し結果を返す"""
    query = args.get("sql_query", "")
    
    # 安全対策: SELECT以外のクエリを禁止
    if not re.match(r"^\s*SELECT", query, re.IGNORECASE):
        return "❌ エラー: データ検索以外の操作（更新・削除など）は許可されていません。"

    # 読み取り専用モードで接続
    try:
        conn = sqlite3.connect(f"file:{config.SQLITE_DB_PATH}?mode=ro", uri=True)
        cursor = conn.cursor()
        
        logger.info(f"🔍 Executing SQL: {query}")
        cursor.execute(query)
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]
        conn.close()

        if not rows:
            return "該当するデータは見つかりませんでした。"

        # 結果を見やすく整形 (Markdownテーブル風、またはJSON)
        result_list = [dict(zip(columns, row)) for row in rows]
        return json.dumps(result_list, ensure_ascii=False, default=str)

    except Exception as e:
        logger.error(f"SQL Execution Error: {e}")
        return f"検索中にエラーが発生しました: {str(e)}"

def execute_get_health_logs(args):
    """体調・排便ログを安全に検索"""
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

# ==========================================
# 3. メイン処理 (Gemini呼び出し)
# ==========================================

def analyze_text_and_execute(text: str, user_id: str, user_name: str) -> str:
    """ユーザーのテキストをGeminiで解析し、適切なツールを実行するか、会話を返す"""
    if not config.GEMINI_API_KEY:
        return None 

    try:
        # モデル初期化 (toolsを指定)
        model = genai.GenerativeModel('gemini-2.5-flash', tools=my_tools)
        
        # プロンプト構築
        prompt = f"""
        ユーザー名: {user_name}
        現在日時: {common.get_now_iso()}
        
        あなたは家庭用アシスタント「セバスチャン」です。
        ユーザーのメッセージから意図を理解し、以下のいずれかを行ってください：
        1. 記録が必要な場合: 適切な記録用ツールを呼び出す。
        2. 情報検索が必要な場合: 提供されたテーブル定義を元にSQLを作成し、`search_database` ツールを呼び出す。
        3. 雑談や挨拶の場合: 親しみやすい口調で返事をする。

        {DB_SCHEMA_INFO}
        
        ユーザーメッセージ: {text}
        """

        # チャットセッション開始
        chat = model.start_chat(enable_automatic_function_calling=True)
        response = chat.send_message(prompt)
        
        if response.text:
            return response.text.strip()
            
    except Exception as e:
        logger.error(f"AI解析エラー: {e}")
        logger.error(traceback.format_exc())
        return "申し訳ありません、処理中にエラーが発生しました🙇"

    return None