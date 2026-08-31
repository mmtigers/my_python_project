# MY_HOME_SYSTEM/services/ai_service.py
import asyncio
import re
import threading
import time
import json
import traceback
from typing import Optional, Dict, Any, List
from datetime import datetime

import google.generativeai as genai
from google.api_core.exceptions import GoogleAPIError, ResourceExhausted
from google.ai.generativelanguage_v1beta.types import content

# Retry logic
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential_jitter,
    retry_if_exception_type,
)

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

# 定数設定
MAX_RETRIES = 3
REQUESTS_PER_MINUTE_LIMIT = 10  # 必要に応じて調整
FALLBACK_MESSAGE = "申し訳ございません。現在AIサービスが混雑しており応答できません。少し時間を置いて再度お試しください。"


# ==========================================
# 0. Rate Limiter (簡易実装)
# ==========================================

class SimpleRateLimiter:
    """
    簡易的なトークンバケット風レートリミッター。
    指定された期間（1分）内のリクエスト数を制限する。
    """
    def __init__(self, limit: int = REQUESTS_PER_MINUTE_LIMIT):
        self.limit = limit
        self.count = 0
        self.last_reset_time = time.time()
        # M-5-3: handlers/line_handler.py の handle_message は着信メッセージごとに
        # asyncio.run(...) で新しいイベントループを生成する。このインスタンスは
        # モジュールレベルの単一シングルトンとして全リクエストで共有されるため、
        # asyncio.Lock だと複数スレッドの別イベントループから同時にロック獲得を
        # 試みた際、コンテンション時に生成される待機Futureが解決不能になり
        # 無期限にハングしうる(該当スレッドの応答が永久に返らなくなる)。
        # スレッドを跨いでも正しく機能する threading.Lock を使う。
        self._lock = threading.Lock()

    async def allow_request(self) -> bool:
        """
        リクエストが許可されるかどうかを判定し、カウンタを更新する。

        Returns:
            bool: リクエスト許可ならTrue, 制限超過ならFalse
        """
        with self._lock:
            now = time.time()
            # 1分経過していればリセット
            if now - self.last_reset_time > 60:
                self.count = 0
                self.last_reset_time = now
            
            if self.count >= self.limit:
                return False
            
            self.count += 1
            return True

# グローバルインスタンス
rate_limiter = SimpleRateLimiter()


# ==========================================
# 1. Tool Functions (実装)
# ==========================================

async def tool_record_child_health(user_id: str, user_name: str, args: Dict[str, Any]) -> str:
    """
    [Tool] 子供の体調を記録する。

    Args:
        user_id (str): LINEユーザーID
        user_name (str): ユーザー名
        args (Dict[str, Any]): ツール引数 (child_name, condition)

    Returns:
        str: 実行結果メッセージ
    """
    child_name = args.get("child_name")
    condition = args.get("condition")
    
    # 名前の正規化 (config.FAMILY_SETTINGS["members"] とのマッチング)
    # ここではAIが正しい名前(configにある名前)を抽出してくると期待する
    
    msg_obj = await line_service.log_child_health(user_id, user_name, child_name, condition)
    return f"記録完了: {msg_obj.text}"


async def tool_record_food(user_id: str, user_name: str, args: Dict[str, Any]) -> str:
    """
    [Tool] 食事を記録する。

    Args:
        user_id (str): LINEユーザーID
        user_name (str): ユーザー名
        args (Dict[str, Any]): ツール引数 (item, category)

    Returns:
        str: 実行結果メッセージ
    """
    item = args.get("item")
    category = args.get("category", "その他")
    
    msg_obj = await line_service.log_food_record(user_id, user_name, category, item, is_manual=True)
    return f"記録完了: {msg_obj.text}"


# AIツールからのSELECTで参照を許可するテーブル (tools_schemaのsql_query説明に記載した4テーブルのみ)
ALLOWED_SEARCH_TABLES = {
    config.SQLITE_TABLE_CHILD,
    config.SQLITE_TABLE_FOOD,
    config.SQLITE_TABLE_SHOPPING,
    config.SQLITE_TABLE_POWER_USAGE,
}


# Issue #224: テーブル名/エイリアスの直後に続きうるSQLキーワード。カンマ結合の
# 各テーブルの直後にエイリアス(`AS name`または`name`)が付くと、次の識別子が
# エイリアスなのかキーワード(WHERE等、テーブル参照の終端)なのかを区別する必要が
# あるため、テーブル参照の終端を示すキーワードをここに列挙しエイリアスとの
# 誤認識を防ぐ。
_SQL_KEYWORDS_NOT_ALIAS = {
    "WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "OFFSET",
    "UNION", "EXCEPT", "INTERSECT", "JOIN", "INNER", "LEFT",
    "RIGHT", "FULL", "OUTER", "CROSS", "ON", "USING",
    "SET", "VALUES", "RETURNING", "WITH", "INTO", "FOR",
}


def _skip_optional_alias(sql: str, pos: int) -> int:
    """`pos`の位置にテーブルエイリアス(`AS name`または`name`)があれば読み飛ばした
    位置を返す。次の識別子がSQLキーワード(WHERE/JOIN等、テーブル参照の終端を示す
    もの)の場合はエイリアスとみなさず読み飛ばさない。"""
    alias_match = re.match(r"\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*)", sql[pos:], flags=re.IGNORECASE)
    if alias_match and alias_match.group(1).upper() not in _SQL_KEYWORDS_NOT_ALIAS:
        return pos + alias_match.end()
    return pos


def _extract_referenced_tables(sql: str) -> List[str]:
    """
    SQL文中で FROM / JOIN が参照するテーブル名をすべて抽出する（簡易パーサ）。

    以下のバイパス経路を塞ぐため、単純な「FROM/JOINの直後の1識別子」だけでなく:
    - `FROM a, b` のような暗黙CROSS JOIN(カンマ結合)の2つ目以降のテーブル
      (Issue #224: 1つ目のテーブルに`FROM a x, b y`のようなエイリアスが付くと、
      識別子の直後がカンマではなくエイリアス文字列になるため、エイリアスを
      読み飛ばしてからカンマ判定する必要がある)
    - `FROM (SELECT ... FROM x) AS y` のようなサブクエリ内の FROM/JOIN
      (re.finditer はSQL全文を走査するため、サブクエリ内の FROM/JOIN も
      自然に検出される)
    も対象にする。
    """
    tables: List[str] = []
    for m in re.finditer(
        r"(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*|\()",
        sql,
        flags=re.IGNORECASE,
    ):
        token = m.group(1)
        if token == "(":
            # サブクエリの開始。中身のFROM/JOINはこのループが引き続き検出する。
            continue
        tables.append(token)

        # 同じFROM/JOIN句内で `, テーブル名` と続く暗黙CROSS JOINを拾う。
        # 各テーブル名の直後にエイリアスが付きうるため、カンマ判定の前に
        # エイリアスを読み飛ばす。
        pos = _skip_optional_alias(sql, m.end())
        while True:
            comma_match = re.match(r"\s*,\s*([A-Za-z_][A-Za-z0-9_]*)", sql[pos:], flags=re.IGNORECASE)
            if not comma_match:
                break
            tables.append(comma_match.group(1))
            pos = _skip_optional_alias(sql, pos + comma_match.end())
    return tables


async def tool_search_db(args: Dict[str, Any]) -> str:
    """
    [Tool] データベースから情報を検索する (読み取り専用)。

    Args:
        args (Dict[str, Any]): ツール引数 (sql_query)

    Returns:
        str: 検索結果またはエラーメッセージ
    """
    sql = args.get("sql_query")
    if not sql:
        return "SQLクエリが指定されていません"

    # 安全対策: SELECT以外は禁止
    if not sql.strip().upper().startswith("SELECT"):
        return "エラー: データ変更操作は許可されていません。"

    # 安全対策: ドキュメントに明記した許可テーブル以外への参照を禁止
    referenced_tables = _extract_referenced_tables(sql)
    if not referenced_tables:
        return "エラー: 参照テーブルを特定できませんでした。"
    disallowed = [t for t in referenced_tables if t not in ALLOWED_SEARCH_TABLES]
    if disallowed:
        logger.warning(f"⚠️ search_db blocked disallowed table(s): {disallowed} (sql={sql!r})")
        return "エラー: 許可されていないテーブルへのアクセスです。"

    try:
        # 読み取り専用で実行
        result = await asyncio.to_thread(common.execute_read_query, sql)
    except Exception as e:
        return f"DB検索エラー: {e}"

    # #180: common.execute_read_query は例外発生時も"検索エラー: ..."という非空文字列を
    # 返す設計(core/database.py参照)のため、以前の`if not rows:`は常に偽となり
    # 到達しないデッドコードだった。加えて、SQL実行時エラーの文字列がそのまま
    # 正常な検索結果としてAIへ渡っていた(呼び出し元はログにも残らず気づけない)。
    # execute_read_queryの内部エラープレフィックスで判定し、警告ログを残した上で
    # エラーであることが分かる形でAIへ返す。
    if result.startswith("検索エラー:"):
        logger.warning(f"⚠️ search_db query failed: {result} (sql={sql!r})")
        return f"DB検索エラー: {result[len('検索エラー:'):].strip()}"

    # 結果を長すぎる場合はカットして返す
    return result[:2000]


# ==========================================
# 2. Tool Definitions (Schema)
# ==========================================

# M-5-1: 下記 search_db スキーマの timestamp 形式説明用のサンプル。
# 実データ(child_health_records等)は本ファイル冒頭で import している
# get_now_iso() でそのまま保存されており、'YYYY-MM-DD HH:MM:SS' のような
# スペース区切りではなく ISO8601 + JSTオフセット('T'区切り, '+09:00') である。
# 説明文とデータの実形式が食い違うと、AIが生成する BETWEEN 等の文字列比較検索が
# ズレて意図した範囲の行を取りこぼす。
_TIMESTAMP_FORMAT_EXAMPLE = get_now_iso()

tools_schema = [
    {
        "function_declarations": [
            {
                "name": "record_child_health",
                "description": "子供の体調や様子を記録します。体温、病状、機嫌などを記録できます。",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "child_name": {"type": "STRING", "description": f"子供の名前。候補: {config.FAMILY_SETTINGS.get('members', [])}"},
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
                            ※ timestampはISO8601形式の文字列 (JSTオフセット付き。例: '{_TIMESTAMP_FORMAT_EXAMPLE}')。
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
# 3. Helper Logic (Retry Wrapper)
# ==========================================

def _log_retry_attempt(retry_state):
    """リトライ時のログ出力用コールバック"""
    exception = retry_state.outcome.exception()
    logger.warning(
        f"⚠️ Gemini API Temporary Failure: {exception}. "
        f"Retrying in {retry_state.next_action.sleep}s... "
        f"(Attempt {retry_state.attempt_number}/{MAX_RETRIES})"
    )

@retry(
    retry=retry_if_exception_type(ResourceExhausted),
    wait=wait_exponential_jitter(initial=2, max=10),
    stop=stop_after_attempt(MAX_RETRIES),
    before_sleep=_log_retry_attempt,
    reraise=True  # 最終的な失敗は呼び出し元でハンドリングするためraiseする
)
async def _call_gemini_api_with_retry(chat_session, prompt: str):
    """
    Gemini APIを呼び出す内部関数。Tenacityによるリトライロジックを含む。
    
    Args:
        chat_session: Gemini ChatSessionオブジェクト
        prompt (str): 送信するプロンプト

    Returns:
        GenerateContentResponse: APIレスポンス
    """
    # 同期メソッドの場合は asyncio.to_thread でラップして実行
    return await asyncio.to_thread(chat_session.send_message, prompt)


# ==========================================
# 4. Main Logic
# ==========================================

async def analyze_text_and_execute(user_id: str, user_name: str, text: str) -> Optional[str]:
    """
    ユーザーの入力を解析し、適切なツールを実行するか、会話応答を返す。
    レートリミットおよびリトライロジックを含む。

    Args:
        user_id (str): LINEユーザーID
        user_name (str): ユーザー名
        text (str): ユーザーからの入力テキスト

    Returns:
        Optional[str]: LINEに返信するメッセージテキスト (Noneの場合は返信なし)
    """
    if not MODEL_NAME or not config.GEMINI_API_KEY:
        return None

    # 1. 簡易レートリミットチェック
    if not await rate_limiter.allow_request():
        logger.warning(f"⚠️ Rate limit exceeded for AI service (User: {user_name})")
        return FALLBACK_MESSAGE

    try:
        model = genai.GenerativeModel(MODEL_NAME, tools=tools_schema)
        
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

        # Geminiセッション開始 (Auto Function Calling無効化)
        chat_manual = model.start_chat(enable_automatic_function_calling=False)
        full_prompt = f"{system_prompt}\n\nユーザーメッセージ: {text}"

        # 2. API呼び出し (Retry Logic適用)
        try:
            response = await _call_gemini_api_with_retry(chat_manual, full_prompt)
        except ResourceExhausted:
            logger.warning("⚠️ Gemini Quota Exhausted after max retries.")
            return FALLBACK_MESSAGE
        except GoogleAPIError as e:
            logger.error(f"❌ Gemini API Fatal Error: {e}")
            return "申し訳ございません。AIサービスで予期せぬエラーが発生しました。"

        if not response or not response.parts:
            logger.error("❌ Empty response from Gemini")
            return "エラー: AIからの応答が空でした。"

        part = response.parts[0]
        
        # --- Handle Function Call ---
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

            # 結果をAIに返して最終回答を生成
            function_response = content.Part(
                function_response=content.FunctionResponse(
                    name=fname,
                    response={"result": tool_result}
                )
            )
            
            # ツールの結果送信もリトライ対象にする (今回は簡易的に同じリトライ関数を利用)
            try:
                final_res = await _call_gemini_api_with_retry(chat_manual, [function_response])
                return final_res.text
            except ResourceExhausted:
                # ツール実行は成功しているが、最終回答生成でコケた場合
                logger.warning("⚠️ Gemini Quota Exhausted during tool output generation.")
                return f"{tool_result}\n(AIの応答生成が制限を超過したため、実行結果のみ表示します)"

        # --- Normal Chat ---
        return response.text

    except Exception as e:
        logger.error(f"AI Analysis Unexpected Error: {e}")
        logger.debug(traceback.format_exc())
        return "申し訳ございません。処理中にエラーが発生しました。"