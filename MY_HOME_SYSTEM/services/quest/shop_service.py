"""services/quest_service.py から分割(Issue #550)。"""
from typing import Any, Dict

from fastapi import HTTPException

import common
from services.quest.locks import (
    ROLE_ADULT,
    _get_purchase_lock,
    _get_user_balance_lock,
    _seconds_since_iso_timestamp,
    logger,
)


class ShopService:
    def process_purchase_reward(self, user_id: str, reward_id: int) -> Dict[str, Any]:
        # 同一ユーザー・同一報酬への同時多重リクエスト(購入確認モーダルの連打等)による
        # 二重購入を防ぐため、DBトランザクションの外側でプロセス内ロックを取得して
        # 処理全体を直列化する(#101)。
        #
        # purchase lock は (user_id, reward_id) 単位の直列化に過ぎず、
        # process_approve_quest/process_cancel_quest が保持する user balance lock
        # とは独立している。購入はゴールドをアトミックな "gold = gold - ?" で減算する
        # ため read-modify-write レース自体は起きないが、承認/取消は
        # "SELECT→Pythonで計算→絶対値でSET" のため、購入のUPDATEコミット後に
        # 承認/取消が古いgoldを基準にした絶対値SETを行うと、購入による減算が
        # 上書きされて消失する(Issue #161)。quest_users を書き換えうる全経路
        # (承認・取消・完了・購入)が対象ユーザー単位で直列化されるよう、
        # purchase lock とは独立に user balance lock も取得する。
        # ロック取得順序は常に balance lock → completion/purchase lock に統一し、
        # 経路間のデッドロックを防ぐ。
        with _get_user_balance_lock(user_id):
            with _get_purchase_lock((user_id, reward_id)):
                return self._process_purchase_reward_locked(user_id, reward_id)

    def _process_purchase_reward_locked(self, user_id: str, reward_id: int) -> Dict[str, Any]:
        with common.get_db_cursor(commit=True) as cur:
            reward = cur.execute("SELECT * FROM reward_master WHERE reward_id = ?", (reward_id,)).fetchone()
            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (user_id,)).fetchone()

            if not reward:
                raise HTTPException(status_code=404, detail="Reward not found")
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # スパムチェック(#101): 購入確認モーダルの「はい」連打で、1回目のレスポンス前に
            # 2回目のリクエストが送られると、ロックが無ければサーバー側は各リクエストを
            # 独立した正当な購入として処理してしまい、残高が足りれば2回とも成功して
            # 二重購入(ゴールド二重消費+アイテム二重取得)が成立する。process_complete_quest
            # と同じ「直近10秒以内の同一操作は拒否」というスパムチェックを行う。
            last_purchase = cur.execute("""
                SELECT redeemed_at FROM reward_history
                WHERE user_id = ? AND reward_id = ?
                ORDER BY redeemed_at DESC LIMIT 1
            """, (user_id, reward_id)).fetchone()

            if last_purchase and last_purchase['redeemed_at']:
                elapsed = _seconds_since_iso_timestamp(last_purchase['redeemed_at'])
                if elapsed is not None and elapsed < 10:
                    raise HTTPException(status_code=429, detail="少し時間を空けてから実行してください")

            target = reward['target'] or 'all'
            if target != 'all':
                is_adult = user['role'] == ROLE_ADULT
                allowed = (
                    (target == 'children' and not is_adult) or
                    (target == 'adults' and is_adult) or
                    (target == user_id)
                )
                if not allowed:
                    raise HTTPException(status_code=403, detail="This reward is not available for you")

            # 残高チェックと減算を単一のアトミックなUPDATEにすることで、
            # 同時多重リクエストによる read-then-write のレースコンディション
            # (二重購入でゴールドが1回分しか減らない不具合) を防ぐ。
            cur.execute(
                "UPDATE quest_users SET gold = gold - ?, updated_at = ? WHERE user_id = ? AND gold >= ?",
                (reward['cost_gold'], common.get_now_iso(), user_id, reward['cost_gold'])
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=400, detail="Not enough gold")

            new_gold = cur.execute(
                "SELECT gold FROM quest_users WHERE user_id = ?", (user_id,)
            ).fetchone()['gold']
            now_iso = common.get_now_iso()

            cur.execute("""
                INSERT INTO reward_history (user_id, reward_id, reward_title, cost_gold, redeemed_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, reward['reward_id'], reward['title'], reward['cost_gold'], now_iso))

            cur.execute("""
                INSERT INTO user_inventory (user_id, reward_id, status, purchased_at)
                VALUES (?, ?, 'owned', ?)
            """, (user_id, reward['reward_id'], now_iso))

            logger.info(f"Reward Purchased & Stored: User={user_id}, Item={reward['title']}")

        return {"status": "purchased", "newGold": new_gold}
