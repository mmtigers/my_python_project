"""クエストの承認・却下・取消(services/quest/quest_service.py から分割。Issue #662)。

`QuestService` が 717行/22メソッドまで育っていたため、「子どもの完了報告を
大人が承認/却下する」「完了報告を取り消す」という別の関心をこちらへ分けた。

完了系(`QuestService`)との共有物は `services/quest/rewards.py` の
`apply_quest_rewards` だけで、クラス同士は互いを参照しない。
"""
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from core.utils import get_now_iso
from core.database import get_db_cursor
import config
import game_logic
from services import switchbot_service
from services.quest.locks import (
    ROLE_ADULT,
    ROLE_CHILD,
    _acquire_user_balance_locks,
    logger,
)
from services.quest.rewards import apply_quest_rewards


class ApprovalService:
    def _get_lock_user_ids_for_history(
        self, history_id: int, primary_user_id: Optional[str] = None
    ) -> List[str]:
        """history_idに対応するquest_history行から、ロック対象ユーザーID一覧を求める。
        兄妹連携クエスト(linked_history_id あり)の場合は相方のuser_idも含める(#98)。

        process_approve_quest/process_reject_quest/process_cancel_questがそれぞれ
        個別に実装していた「対象履歴をpeekして相方を辿り、ロック対象ユーザーを
        まとめる」ロジックを一元化したもの(Issue #293)。

        primary_user_idを指定しない場合はquest_history.user_idから取得し、履歴が
        見つからなければ404を送出する(process_approve_quest/process_reject_quest
        の従来の挙動)。指定した場合はそれを主対象としてそのまま使い、履歴が
        見つからなくても404は送出しない(process_cancel_questの従来の挙動:
        存在確認自体は_process_cancel_quest_locked側に委ねる)。
        """
        with get_db_cursor() as cur:
            hist_peek = cur.execute(
                "SELECT user_id, linked_history_id FROM quest_history WHERE id = ?", (history_id,)
            ).fetchone()

        if primary_user_id is not None:
            lock_user_ids = [primary_user_id]
        else:
            if not hist_peek:
                raise HTTPException(status_code=404, detail="History not found")
            lock_user_ids = [hist_peek['user_id']]

        if hist_peek and hist_peek['linked_history_id'] is not None:
            with get_db_cursor() as cur:
                linked_peek = cur.execute(
                    "SELECT user_id FROM quest_history WHERE id = ?", (hist_peek['linked_history_id'],)
                ).fetchone()
            if linked_peek:
                lock_user_ids.append(linked_peek['user_id'])

        return lock_user_ids

    def process_approve_quest(self, approver_id: str, history_id: int) -> Dict[str, Any]:
        # ロック対象ユーザー(quest_historyの本来の完了者。gold/exp更新の対象)を
        # 先に特定してから、そのユーザー単位でロックを取得する。兄妹連携クエスト
        # (linked_history_id あり)の場合は、承認時に相方の quest_users も
        # カスケードして書き換えるため、相方のユーザーIDも合わせてロックする(#98)。
        lock_user_ids = self._get_lock_user_ids_for_history(history_id)
        with _acquire_user_balance_locks(lock_user_ids):
            return self._process_approve_quest_locked(approver_id, history_id)

    def _process_approve_quest_locked(self, approver_id: str, history_id: int) -> Dict[str, Any]:
        # TV解錠(SwitchBot API 経由の副作用)はトランザクションのコミット後に起動する。
        # 以前は with ブロック内(コミット前)でスレッドを起動していたため、コミットが
        # 失敗(ディスクフル・ロック待ちタイムアウト等)して承認がロールバックされても
        # TVだけが点く可能性があった(Q-L7 は use_item 側だけを修正していた)。
        tv_unlock_quest_id: Optional[int] = None
        with get_db_cursor(commit=True) as cur:
            approver = cur.execute("SELECT role FROM quest_users WHERE user_id = ?", (approver_id,)).fetchone()
            if not approver or approver['role'] != ROLE_ADULT:
                raise HTTPException(status_code=403, detail="承認権限がありません")

            hist = cur.execute("SELECT * FROM quest_history WHERE id = ?", (history_id,)).fetchone()
            if not hist:
                raise HTTPException(status_code=404, detail="History not found")
            if hist['status'] != 'pending':
                raise HTTPException(status_code=400, detail="承認待ちではありません")

            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (hist['user_id'],)).fetchone()
            if not user:
                # #409: 履歴のユーザーがマスタから消えている場合、以前は _apply_quest_rewards 内で
                # TypeError → 500 になっていた(_approve_linked_history 側は None 返却で防御済み)。
                raise HTTPException(status_code=404, detail="User of this history not found")
            quest = cur.execute("SELECT * FROM quest_master WHERE quest_id = ?", (hist['quest_id'],)).fetchone()

            # quest_history.gold_earned/exp_earned は NULL 許容列のため、サービス外で挿入された
            # 行を承認すると user['gold'] + None の TypeError → 500 になっていた。0 扱いにする。
            override_rewards = {
                "gold": hist['gold_earned'] or 0,
                "exp": hist['exp_earned'] or 0
            }

            result = self._apply_quest_rewards(cur, user, quest, get_now_iso(), history_id=history_id, override_rewards=override_rewards)

            attacker_id = hist['user_id']

            # --- 兄妹連携クエスト: 連結された相方の履歴も同一トランザクションでカスケード承認 ---
            # #238: _approve_linked_historyは相方のgold/exp/level/medalを正しく
            # 付与していたが戻り値が無く(-> None)、レスポンスに一切含まれないため
            # フロント側は相方のレベルアップ/メダル獲得演出を出しようがなかった。
            if hist['linked_history_id'] is not None:
                partner_result = self._approve_linked_history(cur, hist['linked_history_id'])
                if partner_result:
                    result['partnerUserId'] = partner_result['user_id']
                    result['partnerLeveledUp'] = partner_result['leveledUp']
                    result['partnerNewLevel'] = partner_result['newLevel']
                    result['partnerEarnedMedals'] = partner_result['earnedMedals']

            # --- TV Lock Feature ---
            # quest はマスタから削除された quest_id の pending 履歴を承認する場合 None になり得る
            # (sync_master_data の DELETE ... NOT IN でマスタ行が消えても quest_history は残るため)。
            if quest and quest['quest_id'] in config.TV_UNLOCK_QUEST_IDS and config.TV_PLUG_DEVICE_ID:
                if user['role'] == ROLE_CHILD:
                    tv_unlock_quest_id = quest['quest_id']

            logger.info(f"Child Quest Approved: Attacker={attacker_id}, Exp={override_rewards['exp']}, Gold={override_rewards['gold']}")

        if tv_unlock_quest_id is not None:
            switchbot_service.trigger_tv_unlock(f"quest_id={tv_unlock_quest_id}")
        return result

    def _approve_linked_history(self, cur, linked_history_id: int) -> Optional[Dict[str, Any]]:
        """兄妹連携クエストの相方側 quest_history 行を承認済みに確定する(冪等)。

        #238: 戻り値で相方のuser_idと_apply_quest_rewardsの結果(leveledUp/newLevel/
        earnedMedals等)を返す。呼び出し元(_process_approve_quest_locked)がこれを
        レスポンスへ含めることで、フロント側が相方のレベルアップ/メダル獲得演出を
        出せるようにするため。
        """
        linked_hist = cur.execute("SELECT * FROM quest_history WHERE id = ?", (linked_history_id,)).fetchone()
        if not linked_hist or linked_hist['status'] != 'pending':
            return None

        linked_user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (linked_hist['user_id'],)).fetchone()
        linked_quest = cur.execute("SELECT * FROM quest_master WHERE quest_id = ?", (linked_hist['quest_id'],)).fetchone()
        if not linked_user:
            return None

        override_rewards = {"gold": linked_hist['gold_earned'] or 0, "exp": linked_hist['exp_earned'] or 0}
        reward_result = self._apply_quest_rewards(cur, linked_user, linked_quest, get_now_iso(), history_id=linked_history_id, override_rewards=override_rewards)
        logger.info(f"Coop Partner Approved: User={linked_hist['user_id']}, HistoryID={linked_history_id}")
        return {"user_id": linked_hist['user_id'], **reward_result}

    def process_reject_quest(self, approver_id: str, history_id: int, reason: Optional[str] = None) -> Dict[str, str]:
        # #228: process_approve_quest と同じユーザー単位ロックに参加させる。
        # 以前はここでロックを一切取得していなかったため、同一history_idに対する
        # 承認と却下がほぼ同時に実行されると、承認側が先にquest_usersへgold/expを
        # 加算・コミットした後に却下のUPDATEがコミットされ、quest_history.statusは
        # 'rejected'になるのに付与済みの報酬は一切ロールバックされない不整合が
        # 生じていた。兄妹連携クエスト(linked_history_id あり)の場合は、相方の
        # quest_users もカスケードして書き換えるため相方のユーザーIDも合わせて
        # ロックする(process_approve_quest/process_cancel_questと同じ理由、#98)。
        lock_user_ids = self._get_lock_user_ids_for_history(history_id)
        with _acquire_user_balance_locks(lock_user_ids):
            return self._process_reject_quest_locked(approver_id, history_id, reason)

    def _process_reject_quest_locked(self, approver_id: str, history_id: int, reason: Optional[str] = None) -> Dict[str, str]:
        with get_db_cursor(commit=True) as cur:
            approver = cur.execute("SELECT role FROM quest_users WHERE user_id = ?", (approver_id,)).fetchone()
            if not approver or approver['role'] != ROLE_ADULT:
                raise HTTPException(status_code=403, detail="承認権限がありません")

            hist = cur.execute("SELECT * FROM quest_history WHERE id = ?", (history_id,)).fetchone()
            if not hist:
                raise HTTPException(status_code=404, detail="History not found")
            if hist['status'] != 'pending':
                raise HTTPException(status_code=400, detail="承認待ちではありません")

            # 却下履歴を残す(以前はDELETEしていたため status='rejected' が実際には
            # 生成されず、process_complete_quest のスパムチェック `status != 'rejected'`
            # が常に成立する死に条件になっていた)。
            # #228: 主対象のUPDATEにも AND status = 'pending' を付ける(連結相方向けの
            # 更新には元々付いていたが主対象には無い非対称な実装だった)。ロック取得に
            # よりこの行の承認/却下は既に直列化されているため二重の安全策ではあるが、
            # UPDATE自体を「pendingのままなら却下」という条件付きにすることで、
            # 万一チェックとUPDATEの間に状態が変化しても却下確定を防ぐ。
            cur.execute("UPDATE quest_history SET status = 'rejected' WHERE id = ? AND status = 'pending'", (history_id,))

            # --- 兄妹連携クエスト: 連結された相方の履歴も同一トランザクションでカスケード却下 ---
            if hist['linked_history_id'] is not None:
                cur.execute("UPDATE quest_history SET status = 'rejected' WHERE id = ? AND status = 'pending'", (hist['linked_history_id'],))
                logger.info(f"Coop Partner Rejected: HistoryID={hist['linked_history_id']}")

            logger.info(f"Quest Rejected: Approver={approver_id}, Target={hist['user_id']}, Reason={reason or '(未指定)'}")
            return {"status": "rejected"}

    def _apply_quest_rewards(self, cur, user, quest, now_iso, history_id=None, override_rewards=None) -> Dict[str, Any]:
        """報酬付与の実体は `services/quest/rewards.py`(完了系と共有)。

        ここに薄いメソッドを残しているのは、テストが
        `monkeypatch.setattr(service, "_apply_quest_rewards", ...)` で遅延や
        失敗を差し込む seam をそのまま維持するため(残高ロックの並行性テストが
        この経路に依存している)。
        """
        return apply_quest_rewards(
            cur, user, quest, now_iso, history_id=history_id, override_rewards=override_rewards
        )

    def process_cancel_quest(self, user_id: str, history_id: int) -> Dict[str, str]:
        # 兄妹連携クエスト(linked_history_id あり)の場合は、取消時に相方の
        # quest_users もカスケードしてロールバックするため、相方のユーザーIDも
        # 合わせてロックする(#98)。history_id が不正/他人の履歴の場合の404/403は
        # 従来どおり _process_cancel_quest_locked 側で検出される。
        lock_user_ids = self._get_lock_user_ids_for_history(history_id, primary_user_id=user_id)
        with _acquire_user_balance_locks(lock_user_ids):
            return self._process_cancel_quest_locked(user_id, history_id)

    def _process_cancel_quest_locked(self, user_id: str, history_id: int) -> Dict[str, str]:
        with get_db_cursor(commit=True) as cur:
            hist = cur.execute("SELECT * FROM quest_history WHERE id = ?", (history_id,)).fetchone()
            if not hist:
                raise HTTPException(status_code=404, detail="History not found")
            if hist['user_id'] != user_id:
                raise HTTPException(status_code=403, detail="User mismatch")

            user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (user_id,)).fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            self._revert_and_delete_history(cur, hist, user, cancelled_by=user_id)

            # --- 兄妹連携クエスト: 連結された相方の履歴も同一トランザクションでカスケード取り消し ---
            linked_id = hist['linked_history_id']
            if linked_id is not None:
                linked_hist = cur.execute("SELECT * FROM quest_history WHERE id = ?", (linked_id,)).fetchone()
                if linked_hist:
                    linked_user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (linked_hist['user_id'],)).fetchone()
                    if linked_user:
                        self._revert_and_delete_history(
                            cur, linked_hist, linked_user, cancelled_by=user_id, cascaded=True
                        )
                        logger.info(f"Coop Partner Cancelled: HistoryID={linked_id}")

            logger.info(f"Quest Cancelled: User={user_id}, HistoryID={history_id}")
        return {"status": "cancelled"}

    def _record_cancellation_audit(
        self, cur, hist, cancelled_by: str, *, cascaded: bool, rewards_reverted: bool
    ) -> None:
        """削除する `quest_history` 行の内容を `quest_cancellation_audit` へ写す(#762)。

        取消は行を物理削除するため、これを残さないと「誰がいつ何を取り消したか」が
        ログファイル以外のどこにも残らず、家族の年代記の原資が復元不能に消える。
        Issue #733 の保持期間削除を有効化した後は「取消で消えた」と「保持期間で
        消えた」の区別もつかなくなる。

        **`quest_history` の意味論は変えない。** 取消済みを年代記に表示するか・
        論理削除に切り替えるかは仕様判断として #762 で未決であり、本メソッドは
        その判断を後から実データに基づいて行えるようにするためだけのものである。
        追記専用で、削除処理の本体(残高ロールバック)より前に呼ぶ。
        """
        # sqlite3.Row の `in` はキーではなく値を走査するため、`.keys()` に対して
        # 判定する必要がある(ここで一度束縛して SIM118 を避ける)。
        hist_keys = hist.keys()
        cur.execute(
            "INSERT INTO quest_cancellation_audit ("
            "history_id, user_id, cancelled_by, quest_id, quest_title, status_before, "
            "completed_at, exp_earned, gold_earned, medals_earned, linked_history_id, "
            "cascaded, rewards_reverted, cancelled_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                hist['id'],
                hist['user_id'],
                cancelled_by,
                hist['quest_id'],
                hist['quest_title'] if 'quest_title' in hist_keys else None,
                hist['status'],
                hist['completed_at'],
                hist['exp_earned'],
                hist['gold_earned'],
                # Q-L3(#409) と同様に、medals_earned を持たない古い行も許容する
                hist['medals_earned'] if 'medals_earned' in hist_keys else None,
                hist['linked_history_id'],
                1 if cascaded else 0,
                1 if rewards_reverted else 0,
                get_now_iso(),
            ),
        )

    def _revert_and_delete_history(
        self, cur, hist, user, cancelled_by: str | None = None, cascaded: bool = False
    ) -> None:
        """
        quest_history 1行を取り消す。approved であれば付与済みの経験値・ゴールドを
        ロールバックしてから削除する。pending / rejected は報酬がまだ付与されて
        いないため、残高には触れず単純に削除する(#97: 以前は status == 'pending'
        以外を一律「付与済み」とみなしてロールバックしていたため、rejected 履歴を
        cancel すると、もらっていない経験値・ゴールドが残高から減算されていた)。

        いずれの経路でも、削除する行の内容を `quest_cancellation_audit` へ写して
        から削除する(#762)。`cancelled_by` を省略した場合は履歴の所有者
        (`hist['user_id']`)を取消要求者として記録する。
        """
        actor = cancelled_by if cancelled_by is not None else hist['user_id']
        if hist['status'] != 'approved':
            # 残高には触れないため rewards_reverted=False
            self._record_cancellation_audit(
                cur, hist, actor, cascaded=cascaded, rewards_reverted=False
            )
            cur.execute("DELETE FROM quest_history WHERE id = ?", (hist['id'],))
            return

        # #356: 以前は max(0, gold - gold_earned) で 0 に飽和させていたため、付与された
        # ゴールドを報酬購入で使い切った後に履歴をキャンセルすると残高が減らず、
        # 再完了で再び付与される「無限ゴールド」が成立していた。付与済みゴールドを
        # 既に消費している(残高 < 付与額)場合は取り消し自体を拒否し、キャンセルが
        # 常に「付与の完全な巻き戻し」になることを保証する。
        gold_earned = hist['gold_earned'] or 0
        current_gold = user['gold'] or 0
        if current_gold < gold_earned:
            raise HTTPException(
                status_code=400,
                detail="獲得したゴールドを既に使用しているため、このクエストは取り消せません",
            )

        new_level, new_exp = game_logic.GameLogic.calc_level_down(
            user['level'], user['exp'], hist['exp_earned'] or 0
        )
        new_gold = current_gold - gold_earned
        # Q-L3(#409): メダルも戻す(履歴に記録が無い古い行は 0 扱い)
        medals_earned = (hist['medals_earned'] if 'medals_earned' in hist.keys() else 0) or 0

        # 残高ロールバックと削除は、取消が拒否されうる上記のチェックを全て通過した
        # 後に行う。監査行もここまで来てから書く(同一トランザクションなので、
        # 後続が失敗すればロールバックされ「取り消していないのに監査行だけ残る」
        # ことはない)。
        self._record_cancellation_audit(
            cur, hist, actor, cascaded=cascaded, rewards_reverted=True
        )
        cur.execute("UPDATE quest_users SET level=?, exp=?, gold=?, medal_count = MAX(0, medal_count - ?), updated_at=? WHERE user_id=?",
                    (new_level, new_exp, new_gold, medals_earned, get_now_iso(), user['user_id']))
        cur.execute("DELETE FROM quest_history WHERE id = ?", (hist['id'],))
