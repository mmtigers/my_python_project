# MY_HOME_SYSTEM/services/line_service.py
import asyncio
from typing import List, Union

# LINE Messaging API Models
from linebot.v3.messaging import TextMessage

import config
from core.logger import setup_logging
from core.utils import get_now_iso, get_today_date_str, get_meal_time_category_from_now
from core.database import save_log_async

# ロガー設定
logger = setup_logging("line_service")

TARGET_MEMBERS = config.FAMILY_SETTINGS["members"]

# Issue #373: DB保存失敗時の返信メッセージの共通プレフィックス。
# 呼び出し元(ai_service のツール関数等)はこのプレフィックスで成否を判別する。
SAVE_FAILED_PREFIX = "⚠️ 記録に失敗しました"

# Issue #377: LINEのTextMessageは1件あたり5000字が上限で、超過するとMessaging APIが
# 400を返す(呼び出し元の`reply_message`はexceptで握るだけなのでユーザーには何も届かない)。
# 4900字を安全マージンとして1メッセージあたりの上限にする。
LINE_TEXT_MAX_CHARS = 4900
# reply/push 1回で送れるメッセージ数の上限(LINE Messaging APIの仕様)。
LINE_MAX_MESSAGES_PER_REPLY = 5


def _line_text_length(text: str) -> int:
    """LINE Messaging APIの文字数カウント方式(UTF-16コードユニット単位)でtextの長さを数える。

    Issue #588: LINE公式ドキュメント(text-character-count)によれば、Messaging APIは
    文字数をUTF-16コードユニット単位で数え、BMP(基本多言語面)外の文字(絵文字等、
    Pythonの1コードポイントがUTF-16ではサロゲートペア=2コードユニットになる文字)は
    2文字分としてカウントされる。以前はPythonの`len(str)`(コードポイント単位)で
    数えていたため、そのような文字を含むテキストでは実際のLINE側カウントより
    少なく見積もってしまい、この関数が「上限内」と判定したメッセージでも
    Messaging API側では上限超過として送信失敗しうる状態だった。
    """
    return len(text.encode('utf-16-le')) // 2


def _take_line_chars(text: str, max_chars: int) -> str:
    """`text`の先頭から、LINE基準(UTF-16コードユニット単位)で`max_chars`文字
    以内に収まる最長のプレフィックスを返す(サロゲートペアの途中では切らない)。
    """
    result: List[str] = []
    total = 0
    for ch in text:
        ch_len = 2 if ord(ch) > 0xFFFF else 1
        if total + ch_len > max_chars:
            break
        result.append(ch)
        total += ch_len
    return "".join(result)


def _split_by_line_char_count(text: str, max_chars: int) -> List[str]:
    """`text`をLINE基準(UTF-16コードユニット単位)で`max_chars`文字以内の
    チャンクに分割する(サロゲートペアを分断しない)。
    """
    chunks: List[str] = []
    remaining = text
    while remaining:
        piece = _take_line_chars(remaining, max_chars)
        if not piece:
            # max_charsが1(UTF-16単位)未満のような呼び出しは想定していないが、
            # 万一空文字しか切り出せない場合に無限ループしないための安全策。
            piece = remaining[0]
        chunks.append(piece)
        remaining = remaining[len(piece):]
    return chunks


def split_text_into_line_messages(text: str) -> Union[TextMessage, List[TextMessage]]:
    """
    Issue #377: 長文を LINE の5000字制限に収まる `TextMessage` へ変換する。

    テキストが `LINE_TEXT_MAX_CHARS` 字(Issue #588: LINE基準のUTF-16コードユニット
    単位)以下ならそのまま単一の `TextMessage` を返す（`handlers.line_handler.reply_message`
    は単一オブジェクト・リストのどちらも受け付けるため、既存呼び出し元の挙動は
    変わらない）。超過する場合のみ `LINE_TEXT_MAX_CHARS` 字ごとに分割した
    `TextMessage` のリストを返し、1回のreply/pushで送れる上限
    (`LINE_MAX_MESSAGES_PER_REPLY`件)を超えるときは末尾を切り詰めて注記を付ける
    （全文を無制限に送り続けることはしない）。
    """
    if _line_text_length(text) <= LINE_TEXT_MAX_CHARS:
        return TextMessage(text=text)

    chunks = _split_by_line_char_count(text, LINE_TEXT_MAX_CHARS)
    if len(chunks) > LINE_MAX_MESSAGES_PER_REPLY:
        chunks = chunks[:LINE_MAX_MESSAGES_PER_REPLY]
        notice = "\n…(文字数上限のため以下省略)"
        last = chunks[-1]
        if _line_text_length(last) + _line_text_length(notice) > LINE_TEXT_MAX_CHARS:
            last = _take_line_chars(last, LINE_TEXT_MAX_CHARS - _line_text_length(notice))
        chunks[-1] = last + notice
    return [TextMessage(text=c) for c in chunks]

# ==========================================
# 1. Logging & Health (Existing)
# ==========================================

async def log_child_health(user_id: str, user_name: str, child_name: str, condition: str) -> TextMessage:
    """子供の体調を記録し、返信メッセージを返す"""
    # Issue #373: save_log_async は Fail-Soft で False を返す(DBロック超過・ディスクフル・
    # NOT NULL違反等)。以前は戻り値を無視して成功メッセージを組み立てていたため、
    # 保存されていないのに「記録しました」と返す無言のデータ欠損が起きていた
    # (line_logic.py 側は H-7 で修正済み。こちらは未修正だった)。
    save_ok = await save_log_async(
        config.SQLITE_TABLE_CHILD,
        ["user_id", "user_name", "child_name", "condition", "timestamp"],
        (user_id, user_name, child_name, condition, get_now_iso())
    )
    if not save_ok:
        logger.error(f"log_child_health の記録保存に失敗しました (user_id={user_id}, child={child_name})")
        return TextMessage(text=f"{SAVE_FAILED_PREFIX}。【{child_name}】{condition} は保存されていません。もう一度お試しください。")
    return TextMessage(text=f"【{child_name}】{condition} を記録しました！🏥")

async def log_food_record(user_id: str, user_name: str, category: str, item: str, is_manual: bool = False) -> TextMessage:
    """食事を記録し、返信メッセージを返す"""
    final_rec = f"{category}: {item}" + (" (手入力)" if is_manual else "")
    # Issue #373: log_child_health と同様に save_log_async の戻り値を確認する。
    # Issue #583: meal_time_categoryは以前固定文字列"Dinner"だった。実際の記録時刻から
    # 時間帯を判定する(categoryはAIが渡す朝食/昼食/夕食等だが、記録時刻と必ずしも
    # 一致しない可能性があるため、時間帯カテゴリは記録時刻そのものを基準にする)。
    save_ok = await save_log_async(
        config.SQLITE_TABLE_FOOD,
        ["user_id", "user_name", "meal_date", "meal_time_category", "menu_category", "timestamp"],
        (user_id, user_name, get_today_date_str(), get_meal_time_category_from_now(), final_rec, get_now_iso())
    )
    if not save_ok:
        logger.error(f"log_food_record の記録保存に失敗しました (user_id={user_id}, item={item})")
        return TextMessage(text=f"{SAVE_FAILED_PREFIX}。{category}「{item}」は保存されていません。もう一度お試しください。")
    return TextMessage(text=f"🍽️ {category}「{item}」を記録しました！")

# 保守性(#410): log_daily_action / log_ohayo / get_daily_health_summary_text は
# 本番・テストのいずれからも呼び出し箇所が無い未使用関数だったため削除した
# (grep incl. tests で確認)。get_daily_health_summary_text内にあった
# cur.connection.row_factory = sqlite3.Row（カーソル生成後の設定で無効な
# no-op行だった点も含む）・bareのexcept:は、関数ごと削除により解消した。
# 体調サマリの取得は handlers/line_logic.py の get_daily_health_summary
# (LINEのcheck_status postbackアクションから実際に呼ばれている実装)を使うこと。
#
# #358: 以前ここにあった LINE 経由の Family Quest コマンド(ステータス/クエスト/
# 承認N/却下N)は、LINE の event.source.user_id(U+32hex)を quest_users.user_id
# (dad/mom/son/daughter)へマッピングする仕組みがリポジトリ内に存在せず、本番では
# 常に「ユーザーデータが見つかりません」「承認権限がありません」になるだけの
# デッドコードだったため撤去した(オーナー判断: LINE経由のクエスト機能は廃止)。
# クエストの確認・完了報告・承認は family-quest フロントエンドを使うこと。