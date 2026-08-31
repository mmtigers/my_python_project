# MY_HOME_SYSTEM/services/line_service.py
import sqlite3
import datetime
import asyncio
from typing import Union

# LINE Messaging API Models
from linebot.v3.messaging import (
    TextMessage,
    FlexMessage
)

import config
import common
from core.logger import setup_logging
from core.utils import get_now_iso, get_today_date_str
from core.database import save_log_async

# Quest Service Integration
from services.quest_service import game_system, quest_service, ROLE_CHILD

# ロガー設定
logger = setup_logging("line_service")

TARGET_MEMBERS = config.FAMILY_SETTINGS["members"]

# ==========================================
# 1. Logging & Health (Existing)
# ==========================================

async def log_child_health(user_id: str, user_name: str, child_name: str, condition: str) -> TextMessage:
    """子供の体調を記録し、返信メッセージを返す"""
    await save_log_async(
        config.SQLITE_TABLE_CHILD,
        ["user_id", "user_name", "child_name", "condition", "timestamp"],
        (user_id, user_name, child_name, condition, get_now_iso())
    )
    return TextMessage(text=f"【{child_name}】{condition} を記録しました！🏥")

async def log_food_record(user_id: str, user_name: str, category: str, item: str, is_manual: bool = False) -> TextMessage:
    """食事を記録し、返信メッセージを返す"""
    final_rec = f"{category}: {item}" + (" (手入力)" if is_manual else "")
    await save_log_async(
        config.SQLITE_TABLE_FOOD,
        ["user_id", "user_name", "meal_date", "meal_time_category", "menu_category", "timestamp"],
        (user_id, user_name, get_today_date_str(), "Dinner", final_rec, get_now_iso())
    )
    return TextMessage(text=f"🍽️ {category}「{item}」を記録しました！")

async def log_daily_action(user_id: str, user_name: str, action_type: str, value: str) -> None:
    """日常動作（外出・面会など）を記録 (返信なし)"""
    logger.info(f"Daily Action: {user_name} -> {action_type}: {value}")
    # 必要に応じてDB保存処理を追加

async def log_ohayo(user_id: str, user_name: str, message: str, keyword: str) -> None:
    """おはようメッセージの記録"""
    await save_log_async(
        "communication_logs",
        ["user_id", "user_name", "message", "timestamp", "recognized_keyword"], 
        (user_id, user_name, message, get_now_iso(), keyword)
    )

def get_daily_health_summary_text() -> str:
    """今日の体調記録サマリを取得してテキストで返す"""
    today_str = get_today_date_str()
    summary_lines = []
    
    try:
        # 読み取り専用で接続
        with common.get_db_cursor() as cur:
            # RowFactoryはcommon側で設定されていない場合があるため、dict化は手動で行うかcommonに依存
            cur.connection.row_factory = sqlite3.Row
            
            for name in TARGET_MEMBERS:
                row = cur.execute(f"""
                    SELECT condition, timestamp FROM {config.SQLITE_TABLE_CHILD}
                    WHERE child_name = ? AND timestamp LIKE ?
                    ORDER BY id DESC LIMIT 1
                """, (name, f"{today_str}%")).fetchone()
                
                if row:
                    try:
                        ts = row["timestamp"]
                        if "T" in ts: dt = datetime.datetime.fromisoformat(ts)
                        else: dt = datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                        time_str = dt.strftime("%H:%M")
                    except:
                        time_str = "??:??"
                    status = row["condition"]
                    icon = "✅" if "元気" in status else "⚠️"
                    summary_lines.append(f"{icon} {name}: {status} ({time_str})")
                else:
                    summary_lines.append(f"❓ {name}: (未記録)")
    except Exception as e:
        logger.error(f"DB Read Error: {e}")
        return "⚠️ データ取得エラー"

    return "\n".join(summary_lines)

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

async def get_active_quests_message(user_id: str) -> Union[TextMessage, FlexMessage]:
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
            target = q['target']
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
        return TextMessage(text="\n".join(lines))

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