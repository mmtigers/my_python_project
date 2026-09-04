# MY_HOME_SYSTEM/services/line_service.py
import asyncio
from typing import List, Union

# LINE Messaging API Models
from linebot.v3.messaging import (
    TextMessage,
    FlexMessage
)

import config
from core.logger import setup_logging
from core.utils import get_now_iso, get_today_date_str
from core.database import save_log_async

# Quest Service Integration
from services.quest_service import game_system, quest_service, ROLE_CHILD

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


def split_text_into_line_messages(text: str) -> Union[TextMessage, List[TextMessage]]:
    """
    Issue #377: 長文を LINE の5000字制限に収まる `TextMessage` へ変換する。

    テキストが `LINE_TEXT_MAX_CHARS` 字以下ならそのまま単一の `TextMessage` を返す
    （`handlers.line_handler.reply_message` は単一オブジェクト・リストのどちらも
    受け付けるため、既存呼び出し元の挙動は変わらない）。超過する場合のみ
    `LINE_TEXT_MAX_CHARS` 字ごとに分割した `TextMessage` のリストを返し、1回の
    reply/pushで送れる上限(`LINE_MAX_MESSAGES_PER_REPLY`件)を超えるときは末尾を
    切り詰めて注記を付ける（全文を無制限に送り続けることはしない）。
    """
    if len(text) <= LINE_TEXT_MAX_CHARS:
        return TextMessage(text=text)

    chunks = [text[i:i + LINE_TEXT_MAX_CHARS] for i in range(0, len(text), LINE_TEXT_MAX_CHARS)]
    if len(chunks) > LINE_MAX_MESSAGES_PER_REPLY:
        chunks = chunks[:LINE_MAX_MESSAGES_PER_REPLY]
        notice = "\n…(文字数上限のため以下省略)"
        last = chunks[-1]
        if len(last) + len(notice) > LINE_TEXT_MAX_CHARS:
            last = last[:LINE_TEXT_MAX_CHARS - len(notice)]
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
    save_ok = await save_log_async(
        config.SQLITE_TABLE_FOOD,
        ["user_id", "user_name", "meal_date", "meal_time_category", "menu_category", "timestamp"],
        (user_id, user_name, get_today_date_str(), "Dinner", final_rec, get_now_iso())
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

# ==========================================
# 2. Family Quest Integration (New)
# ==========================================

async def get_user_status_message(user_id: str) -> Union[TextMessage, FlexMessage]:
    """ユーザーのステータス情報を取得して返す"""
    try:
        data = await asyncio.to_thread(game_system.get_all_view_data)
        users = data.get("users", [])
        target_user = next((u for u in users if u["user_id"] == user_id), None)

        if not target_user:
            return TextMessage(text="⚠️ ユーザーデータが見つかりません。登録を確認してください。")

        msg = (
            f"👤 {target_user['name']} ({target_user['job_class']})\n"
            f"━━━━━━━━━━━━━━\n"
            f"Lv. {target_user['level']}\n"
            f"💰 {target_user['gold']} G\n"
            f"✨ {target_user['exp']} EXP\n"
            f"━━━━━━━━━━━━━━\n"
            f"次のレベルまで: {target_user['nextLevelExp']} EXP"
        )
        return TextMessage(text=msg)

    except Exception as e:
        logger.error(f"Status fetch error: {e}")
        return TextMessage(text="⚠️ ステータスの取得に失敗しました。")

async def get_active_quests_message(user_id: str) -> Union[TextMessage, FlexMessage, List[TextMessage]]:
    """受注可能なクエスト一覧を返す"""
    try:
        data = await asyncio.to_thread(game_system.get_all_view_data)
        quests = data.get("quests", [])
        
        if not quests:
            return TextMessage(text="現在受注できるクエストはありません🛌")

        # 兄妹連携クエスト(target='siblings')は特定のuser_idとは一致しないため、
        # 単純な != user_id 比較では常にスキップされ誰にも表示されなかった。
        # 対象は子供(role_child)全員であり、家族画面(FamilyDashboard.tsx等)の
        # 対象判定と同じ意味付けにする。
        users = data.get("users", [])
        user_role = next((u.get('role') for u in users if u.get('user_id') == user_id), None)

        lines = ["⚔️ 本日のクエスト"]
        for q in quests:
            target = q['target_user']
            if target == 'siblings':
                if user_role != ROLE_CHILD:
                    continue
            elif target != 'all' and target != user_id:
                continue
                
            bonus = ""
            if q.get('bonus_gold', 0) > 0:
                bonus = " 🔥ボーナス中!"
            
            lines.append(f"・{q['title']} (💰{q['gold_gain']}{bonus})")
        
        lines.append("\n終わったら「○○完了」と報告してね！")
        # Issue #377: クエスト件数が多いと5000字を超えうるため分割する。
        # `line_handler.reply_message`は非list渡しをlist化するのと同様、list渡しもそのまま
        # 使えるため、呼び出し元(line_handler.py)の変更は不要。
        return split_text_into_line_messages("\n".join(lines))

    except Exception as e:
        logger.error(f"Quest fetch error: {e}")
        return TextMessage(text="⚠️ クエスト情報の取得に失敗しました。")

async def process_approval_command(approver_id: str, text: str) -> TextMessage:
    """承認/却下コマンドの処理"""
    try:
        parts = text.replace("_", " ").split()
        if len(parts) < 2:
            return TextMessage(text="⚠️ IDを指定してください (例: 承認 123)")
        
        cmd = parts[0]
        history_id = int(parts[1])

        if "承認" in cmd:
            res = await asyncio.to_thread(
                quest_service.process_approve_quest, approver_id, history_id
            )
            msg = f"✅ 承認しました！\n獲得: {res['earnedExp']}EXP, {res['earnedGold']}G"
            if res.get('leveledUp'):
                msg += f"\n🎉 レベルアップ！ Lv.{res['newLevel']}"
            return TextMessage(text=msg)
            
        elif "却下" in cmd:
            await asyncio.to_thread(
                quest_service.process_reject_quest, approver_id, history_id
            )
            return TextMessage(text="🚫 却下しました。")
            
    except ValueError:
        return TextMessage(text="⚠️ IDは数字で指定してください。")
    except Exception as e:
        detail = str(e)
        if hasattr(e, 'detail'): detail = e.detail
        return TextMessage(text=f"⚠️ エラー: {detail}")

    return TextMessage(text="❓ 不明なコマンドです")