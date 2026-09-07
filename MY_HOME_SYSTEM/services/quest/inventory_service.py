"""services/quest_service.py から分割(Issue #550)。"""
import datetime
import math
from typing import Any, Dict, Tuple

from fastapi import HTTPException

import common
import config
from core import sound_manager
from services import notification_service
from services.quest.locks import (
    JST,
    _get_item_use_lock,
    _get_youtube_cooldown_remaining_seconds,
    _is_youtube_cooldown_enforced,
)


class InventoryService:
    def get_user_inventory(self, user_id: str) -> Dict[str, Any]:
        with common.get_db_cursor() as cur:
            sql = """
                SELECT ui.id, ui.reward_id, ui.status, ui.purchased_at, ui.used_at,
                       rm.title, rm.description as desc, rm.icon_key as icon, rm.category
                FROM user_inventory ui
                JOIN reward_master rm ON ui.reward_id = rm.reward_id
                WHERE ui.user_id = ? AND ui.status = 'owned'
                ORDER BY ui.purchased_at DESC
            """
            rows = cur.execute(sql, (user_id,)).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                # フロントエンド(InventoryList.tsx)がYouTube系ごほうび券のクールダウン
                # UIを出し分けられるよう、判定ロジックはconfig側に集約したままフラグだけ渡す。
                item['is_youtube_reward'] = item['reward_id'] in config.YOUTUBE_REWARD_IDS
                items.append(item)

            cooldown_enforced = _is_youtube_cooldown_enforced()
            youtube_cooldown_remaining_seconds = (
                _get_youtube_cooldown_remaining_seconds(cur, user_id) if cooldown_enforced else 0
            )

            # 猶予期間中(cooldown_enforced=False)は、実際に制限が始まる日を予告する情報を
            # 返す。フロントエンド(InventoryList.tsx)はこれを見て告知バナーを表示する。
            # 施行開始後・クールダウン対象IDが未設定の場合はNone。
            youtube_cooldown_announcement = None
            if not cooldown_enforced and config.YOUTUBE_REWARD_IDS:
                days_remaining = (
                    config.YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM - datetime.datetime.now(JST).date()
                ).days
                youtube_cooldown_announcement = {
                    "starts_on": config.YOUTUBE_REWARD_COOLDOWN_ENFORCE_FROM.isoformat(),
                    "days_remaining": max(0, days_remaining),
                }

        return {
            "items": items,
            "youtube_cooldown_remaining_seconds": youtube_cooldown_remaining_seconds,
            "youtube_cooldown_announcement": youtube_cooldown_announcement,
        }

    def use_item(self, user_id: str, inventory_id: int) -> Dict[str, str]:
        # Issue #544: ユーザー単位ロックの保持範囲はDB更新(コミット)までに限定する。
        # 以前は _use_item_locked の末尾で同期の LINE push(最大15秒)まで実行していたため、
        # LINE が遅い/タイムアウトした場合に同一ユーザーの次の use_item がその往復の間
        # 直列化されていた。外部副作用はロック解放後に行う。
        with _get_item_use_lock(user_id):
            result, msg = self._use_item_locked(user_id, inventory_id)

        # 外部副作用(LINE送信・効果音)はコミット後・ロック解放後に実行する。以前はトランザクション
        # 内でLINE APIの往復を待っていたため、その間SQLiteの書き込みロックを保持し続け、
        # 他のwriterが "database is locked" 待ちになっていた(Q-L7)。
        notification_service.send_push(
            user_id=config.LINE_USER_ID,
            messages=[{"type": "text", "text": msg}]
        )
        sound_manager.play("quest_clear")

        return result

    def _use_item_locked(self, user_id: str, inventory_id: int) -> Tuple[Dict[str, str], str]:
        """
        アイテムを使用し、即座に消費を確定する(親の承認は不要)。
        戻り値は (APIレスポンス, 通知メッセージ)。通知の送信は呼び出し側(use_item)が
        ロック解放後に行う(#544)。
        """
        with common.get_db_cursor(commit=True) as cur:
            sql = """
                SELECT ui.*, rm.title, qu.name as user_name
                FROM user_inventory ui
                JOIN reward_master rm ON ui.reward_id = rm.reward_id
                JOIN quest_users qu ON ui.user_id = qu.user_id
                WHERE ui.id = ?
            """
            item = cur.execute(sql, (inventory_id,)).fetchone()

            if not item:
                raise HTTPException(404, "Item not found")
            if item['user_id'] != user_id:
                raise HTTPException(403, "Not your item")
            if item['status'] != 'owned':
                raise HTTPException(400, "Cannot use this item")

            # 連続視聴による目の負担を防ぐため、YouTube系ごほうび券は前回使用から
            # YOUTUBE_REWARD_COOLDOWN_SECONDS(15分)経過するまで再使用できないようにする。
            # ただしYOUTUBE_REWARD_COOLDOWN_ENFORCE_FROMを迎えるまでは実際には拒否しない
            # (いきなり制限がかかると子どもが困惑するため、事前に予告バナーのみ表示する)。
            if item['reward_id'] in config.YOUTUBE_REWARD_IDS and _is_youtube_cooldown_enforced():
                cooldown_remaining = _get_youtube_cooldown_remaining_seconds(cur, user_id)
                if cooldown_remaining > 0:
                    remaining_minutes = math.ceil(cooldown_remaining / 60)
                    raise HTTPException(
                        429,
                        f"YouTubeのごほうび券は、目を休めるためあと{remaining_minutes}分ほど使えません",
                    )

            now_iso = common.get_now_iso()

            # #369: SELECT→Python判定→無条件UPDATE では、WALで読み取りがブロックされない
            # ため連打された2リクエストが両方 'owned' を読み、両方が消費処理・履歴INSERT・
            # 通知を実行していた(二重使用)。status='owned' を条件に含めた条件付きUPDATEに
            # し、rowcount==0(先行リクエストが既に消費済み)なら400で拒否する。
            cur.execute("""
                UPDATE user_inventory
                SET status = 'consumed', used_at = ?
                WHERE id = ? AND status = 'owned'
            """, (now_iso, inventory_id))
            if cur.rowcount == 0:
                raise HTTPException(400, "Cannot use this item")

            log_title = f"アイテム使用: {item['title']}"
            cur.execute("""
                INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
                VALUES (?, 0, ?, 0, 0, ?, 'approved')
            """, (item['user_id'], log_title, now_iso))

            msg = f"🎒 {item['user_name']}が「{item['title']}」を使用しました。"

        return {"status": "consumed", "message": "つかいました！"}, msg


# アイテム使用は承認フローを介さず即時確定する、Family Quest内で唯一
# GameSystem(quest/user/shop_service)の合成に含まれないシングルトン。
inventory_service = InventoryService()
