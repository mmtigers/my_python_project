# MY_HOME_SYSTEM/services/ai_service.py
import asyncio
import json
import traceback
from typing import Optional, Dict, Any, List
from datetime import datetime

import google.generativeai as genai
from google.api_core.exceptions import GoogleAPIError

import config
import common
from core.logger import setup_logging
from core.utils import get_now_iso

# Service連携
from services import line_service

# ロガー設定
logger = setup_logging("ai_service")

# === Gemini 初期化 ===
if config.GEMINI_API_KEY:
    genai.configure(api_key=config.GEMINI_API_KEY)
    # Gemini 1.5 Flash / 2.0 Flash を推奨
    MODEL_NAME = 'gemini-2.0-flash' 
else:
    logger.warning("⚠️ GEMINI_API_KEYが設定されていません。AI機能は無効です。")
    MODEL_NAME = None

# ==========================================
# 1. Tool Functions (実装)
# ==========================================

async def tool_record_child_health(user_id: str, user_name: str, args: Dict[str, Any]) -> str:
    """
    [Tool] 子供の体調を記録する
    Args:
        child_name (str): 子供の名前 (呼び捨て可)
        condition (str): 症状や様子 (例: 37.5度の熱, 元気, 咳が出ている)
    """
    child_name = args.get("child_name")
    condition = args.get("condition")
    
    # 名前の正規化 (config.FAMILY_SETTINGS["members"] とのマッチング)
    # 簡易的に "長男" -> "マサヒロJr" のような変換が必要ならここで行うか、AIに任せる
    # ここではAIが正しい名前(configにある名前)を抽出してくると期待する
    
    msg_obj = await line_service.log_child_health(user_id, user_name, child_name, condition)
    return f"記録完了: {msg_obj.text}"

async def tool_record_food(user_id: str, user_name: str, args: Dict[str, Any]) -> str:
    """
    [Tool] 食事を記録する
    Args:
        item (str): 食べたもの
        category (str): カテゴリ (朝食/昼食/夕食/間食/自炊/外食 など)
    """
    item = args.get("item")
    category = args.get("category", "その他")
    
    msg_obj = await line_service.log_food_record(user_id, user_name, category, item, is_manual=True)
    return f"記録完了: {msg_obj.text}"

async def tool_search_db(args: Dict[str, Any]) -> str:
    """
    [Tool] データベースから情報を検索する (読み取り専用)
    Args:
        query_intent (str): 検索したい内容の要約 (SQL生成はAIに任せず、定型クエリを使う方針に変更も可だが、ここでは簡易RAG的にSQL実行を許可する)
        sql_query (str): 実行したいSQLiteのSELECT文 (AIが生成)
    """
    sql = args.get("sql_query")
    if not sql: return "SQLクエリが指定されていません"
    
    # 安全対策: SELECT以外は禁止
    if not sql.strip().upper().startswith("SELECT"):
        return "エラー: データ変更操作は許可されていません。"

    try:
        # 読み取り専用で実行
        rows = await asyncio.to_thread(common.execute_read_query, sql)
        if not rows:
            return "該当するデータは見つかりませんでした。"
        # 結果を文字列化して返す（長すぎる場合はカット）
        return str(rows)[:2000]
    except Exception as e:
        return f"DB検索エラー: {e}"

# ==========================================
# 2. Tool Definitions (Schema)
# ==========================================

tools_schema = [
    {
        "function_declarations": [
            {
                "name": "record_child_health",
                "description": "子供の体調や様子を記録します。体温、病状、機嫌などを記録できます。",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "child_name": {"type": "STRING", "description": f"子供の名前。候補: {config.FAMILY_SETTINGS['members']}"},
                        "condition": {"type": "STRING", "description": "体調の状態、体温、具体的な症状など"}
                    },
                    "required": ["child_name", "condition"]
                }
            },
            {
                "name": "record_food",
                "description": "食事の内容を記録します。",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "item": {"type": "STRING", "description": "食べたメニュー名"},
                        "category": {"type": "STRING", "description": "食事カテゴリ (朝食, 昼食, 夕食, おやつ, 外食, 自炊)"}
                    },
                    "required": ["item"]
                }
            },
            {
                "name": "search_db",
                "description": "過去の記録（体調、食事、センサーログ、買い物履歴）をデータベースから検索します。",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "sql_query": {
                            "type": "STRING", 
                            "description": f"""
                            実行するSQLiteのSELECT文。テーブル一覧:
                            - {config.SQLITE_TABLE_CHILD} (timestamp, child_name, condition)
                            - {config.SQLITE_TABLE_FOOD} (timestamp, menu_category)
                            - {config.SQLITE_TABLE_SHOPPING} (order_date, item_name, price)
                            - {config.SQLITE_TABLE_POWER_USAGE} (timestamp, device_name, wattage)
                            ※ timestampは 'YYYY-MM-DD HH:MM:SS' 形式の文字列。
                            """
                        }
                    },
                    "required": ["sql_query"]
                }
            }
        ]
    }
]

# ==========================================
# 3. Main Logic
# ==========================================

async def analyze_text_and_execute(user_id: str, user_name: str, text: str) -> Optional[str]:
    """
    ユーザーの入力を解析し、適切なツールを実行するか、会話応答を返す。
    Returns:
        str: LINEに返信するメッセージテキスト (Noneの場合は返信なし)
    """
    if not MODEL_NAME or not config.GEMINI_API_KEY:
        return None

    try:
        # --- 1. Generate Content (Call Gemini) ---
        model = genai.GenerativeModel(MODEL_NAME, tools=tools_schema)
        
        # System Prompt Construction
        system_prompt = f"""
        あなたは「セバスチャン」という名前の、有能で忠実な執事です。
        ユーザー（{user_name}様）の生活をサポートするために、会話を通じて記録を行ったり、情報を検索したりします。
        
        【現在情報】
        - 現在時刻: {get_now_iso()}
        - ユーザー名: {user_name}
        
        【振る舞いの指針】
        - 丁寧で落ち着いた口調（です・ます調）で話してください。
        - ユーザーが記録を求めた場合は、適切なツールを呼び出してください。
        - ユーザーが質問をした場合は、search_dbツールを使って過去のデータを検索してください。
        - ツールを呼び出した後は、その結果に基づいて「承知いたしました。〜を記録しました。」のように完了報告をしてください。
        - 雑談の場合は、気の利いた返答を短めに返してください。
        """

        # API Call (Non-streaming for simpler function handling)
        chat = model.start_chat(enable_automatic_function_calling=True)
        
        # User Message
        response = await asyncio.to_thread(
            chat.send_message,
            f"{system_prompt}\n\nユーザーメッセージ: {text}"
        )

        # Gemini SDKの automatic_function_calling は内部でツール実行まで行ってくれるが、
        # Python関数との紐付け（マッピング）が必要。
        # ここでは手動制御(Manual Control)の方が既存のService層と非同期連携しやすいため、
        # function_call の有無をチェックして自前で実行するパターンを採用する。
        # ※ ただし上記 start_chat(enable_automatic_function_calling=True) だとSDKが勝手に実行しようとしてエラーになる可能性があるため、
        #    Falseにして自前でパースする。
        
        # Re-initialize without auto execution for manual handling
        chat_manual = model.start_chat(enable_automatic_function_calling=False)
        response = await asyncio.to_thread(
            chat_manual.send_message,
            f"{system_prompt}\n\nユーザーメッセージ: {text}"
        )
        
        part = response.parts[0]
        
        # --- 2. Handle Function Call ---
        if part.function_call:
            fc = part.function_call
            fname = fc.name
            fargs = dict(fc.args)
            
            logger.info(f"🤖 AI Triggered Tool: {fname} args={fargs}")
            
            tool_result = ""
            if fname == "record_child_health":
                tool_result = await tool_record_child_health(user_id, user_name, fargs)
            elif fname == "record_food":
                tool_result = await tool_record_food(user_id, user_name, fargs)
            elif fname == "search_db":
                tool_result = await tool_search_db(fargs)
            else:
                tool_result = "エラー: 未知のツールが呼び出されました。"

            # ツールの実行結果をAIに返して、最終的な回答を生成させる
            # (FunctionResponse を送る)
            from google.ai.generativelanguage_v1beta.types import content
            
            function_response = content.Part(
                function_response=content.FunctionResponse(
                    name=fname,
                    response={"result": tool_result}
                )
            )
            
            # Send result back to model
            final_res = await asyncio.to_thread(
                chat_manual.send_message,
                [function_response]
            )
            return final_res.text

        # --- 3. No Function Call (Normal Chat) ---
        return response.text

    except Exception as e:
        logger.error(f"AI Analysis Error: {e}")
        logger.debug(traceback.format_exc())
        return "申し訳ございません。処理中にエラーが発生しました。"