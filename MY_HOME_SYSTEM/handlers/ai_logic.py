import google.generativeai as genai
from google.generativeai.types import FunctionDeclaration, Tool
import json
import datetime
import traceback
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
# 1. ツール定義 (関数宣言方式)
# ==========================================

def declare_child_health(child_name: str, condition: str, is_emergency: bool = False):
    """子供の体調や怪我、様子を記録する。

    Args:
        child_name: 子供の名前 (例: たろう, はな, 子供)
        condition: 症状や状態 (例: 38度の熱, 鼻水が出ている, 元気いっぱい)
        is_emergency: 熱や怪我など、心配な症状の場合はTrue
    """
    pass

def declare_shopping(item_name: str, price: int, date_str: str = None):
    """買い物や支出を記録する。

    Args:
        item_name: 買ったものや店名 (例: スーパーの食材, コンビニ, ガソリン)
        price: 金額 (円)
        date_str: 日付 (YYYY-MM-DD形式)。指定がなければ今日。
    """
    pass

def declare_defecation(condition: str, note: str = ""):
    """排便やトイレ、お腹の調子を記録する。

    Args:
        condition: 状態 (例: 普通のうんち, 下痢気味, 便秘)
        note: 補足メモ (任意)
    """
    pass

# ツールセット
my_tools = [declare_child_health, declare_shopping, declare_defecation]

# ==========================================
# 2. 実行ロジック (DB保存)
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
    
    # 【修正箇所】AIが float (3000.0) で返すことがあるため、int に強制変換
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

# ==========================================
# 3. メイン処理 (Gemini呼び出し)
# ==========================================

def analyze_text_and_execute(text: str, user_id: str, user_name: str) -> str:
    """ユーザーのテキストをGeminiで解析し、適切なツールを実行するか、会話を返す"""
    if not config.GEMINI_API_KEY:
        return None 

    try:
        model = genai.GenerativeModel('gemini-2.5-flash', tools=my_tools)
        
        prompt = f"""
        ユーザー名: {user_name}
        現在日時: {common.get_now_iso()}
        
        あなたは家庭用アシスタントです。ユーザーのメッセージから情報を抽出し、適切な関数を呼び出してください。
        関数を呼び出す必要がない雑談や挨拶の場合は、親しみやすい口調で返事をしてください。
        
        ユーザーメッセージ: {text}
        """

        response = model.generate_content(prompt)
        
        if response.parts:
            for part in response.parts:
                if fn := part.function_call:
                    tool_name = fn.name
                    args = dict(fn.args)
                    logger.info(f"🤖 AI Tool Call: {tool_name} args={args}")
                    
                    if tool_name == "declare_child_health":
                        return execute_child_health(args, user_id, user_name)
                    elif tool_name == "declare_shopping":
                        return execute_shopping(args, user_id, user_name)
                    elif tool_name == "declare_defecation":
                        return execute_defecation(args, user_id, user_name)
        
        if response.text:
            return response.text
            
    except Exception as e:
        logger.error(f"AI解析エラー: {e}")
        logger.error(traceback.format_exc())
        return None 

    return None