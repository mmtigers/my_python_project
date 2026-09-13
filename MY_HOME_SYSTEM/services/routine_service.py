"""デイリールーティン(すごろく形式の生活導線UI)のサービス層。

routers/routine_router.py はパース・検証のみを行い、ロジックはここに委譲する
(CLAUDE.mdのレイヤリング規約)。quest_users(gold/exp/level)への書き込みを伴うため、
services/quest/locks.py の user balance lock を quest_service と共用し、
クエスト完了/承認と同一ユーザーへの並行更新によるlost updateを防ぐ。
"""
import datetime
import json
from typing import Any, Dict, Optional

from fastapi import HTTPException

import common
import game_logic
from core import sound_manager
from routine_data import (
    FULL_BONUS_EXP, FULL_BONUS_GOLD, ROUTINE_FLOWS, RoutineFlow,
    get_checkpoint_index, get_effective_checkpoint_time,
)
from services.quest.locks import JST, _get_user_balance_lock, logger


class RoutineService:
    def _today_str(self, now: datetime.datetime) -> str:
        return now.strftime('%Y-%m-%d')

    def _is_flow_started_today(self, flow: RoutineFlow, now: datetime.datetime) -> bool:
        if now.weekday() not in flow['day_of_week']:
            return False
        hour, minute = map(int, flow['start_trigger_time'].split(':'))
        trigger = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return now >= trigger

    def _empty_statuses(self, flow: RoutineFlow) -> Dict[str, str]:
        return {
            step['key']: ('current' if idx == 0 else 'locked')
            for idx, step in enumerate(flow['steps'])
        }

    def _row_to_progress(self, row) -> Dict[str, Any]:
        return {
            'id': row['id'],
            'current_step_index': row['current_step_index'],
            'in_free_time': bool(row['in_free_time']),
            'steps_status': json.loads(row['steps_status']),
            'bonus_gold': row['bonus_gold'],
            'bonus_exp': row['bonus_exp'],
        }

    def _get_or_create_progress(self, cur, user_id: str, flow_key: str, flow: RoutineFlow, date_str: str) -> Dict[str, Any]:
        row = cur.execute(
            "SELECT * FROM routine_progress WHERE user_id=? AND flow_key=? AND progress_date=?",
            (user_id, flow_key, date_str),
        ).fetchone()
        if row:
            return self._row_to_progress(row)

        now_iso = common.get_now_iso()
        cur.execute("""
            INSERT INTO routine_progress
                (user_id, flow_key, progress_date, current_step_index, in_free_time,
                 steps_status, bonus_gold, bonus_exp, created_at, updated_at)
            VALUES (?, ?, ?, 0, 0, ?, 0, 0, ?, ?)
        """, (
            user_id, flow_key, date_str,
            json.dumps(self._empty_statuses(flow), ensure_ascii=False),
            now_iso, now_iso,
        ))
        row = cur.execute(
            "SELECT * FROM routine_progress WHERE user_id=? AND flow_key=? AND progress_date=?",
            (user_id, flow_key, date_str),
        ).fetchone()
        return self._row_to_progress(row)

    def _save_progress(self, cur, progress: Dict[str, Any]) -> None:
        cur.execute("""
            UPDATE routine_progress
            SET current_step_index=?, in_free_time=?, steps_status=?, bonus_gold=?, bonus_exp=?, updated_at=?
            WHERE id=?
        """, (
            progress['current_step_index'], int(progress['in_free_time']),
            json.dumps(progress['steps_status'], ensure_ascii=False),
            progress['bonus_gold'], progress['bonus_exp'],
            common.get_now_iso(), progress['id'],
        ))

    def _grant_bonus(self, cur, user_id: str, gold: int, exp: int) -> Dict[str, Any]:
        user = cur.execute("SELECT * FROM quest_users WHERE user_id=?", (user_id,)).fetchone()
        if not user:
            return {"leveled_up": False, "new_level": None}
        new_level, new_exp_val, leveled_up = game_logic.GameLogic.calc_level_progress(
            user['level'], user['exp'], exp
        )
        cur.execute(
            "UPDATE quest_users SET level=?, exp=?, gold=?, updated_at=? WHERE user_id=?",
            (new_level, new_exp_val, user['gold'] + gold, common.get_now_iso(), user_id),
        )
        if leveled_up:
            sound_manager.play("level_up")
        return {"leveled_up": leveled_up, "new_level": new_level}

    def _apply_forced_transition(
        self, cur, user_id: str, flow_key: str, flow: RoutineFlow, progress: Dict[str, Any], now: datetime.datetime
    ) -> Dict[str, Any]:
        """チェックポイント(自由時間の終了予定時刻)を過ぎていれば、未完了ステップを
        「まだだよ」に変え、チェックポイントより前のステップの達成率に応じたボーナスを
        付与したうえでチェックポイントの次のステップへ進める。

        current_step_indexがチェックポイントを既に通過していれば何もしない(冪等)ため、
        GET(状態取得)・POST(ステップ完了)のどちらからも安全に呼べる。
        """
        checkpoint_idx = get_checkpoint_index(flow)
        if checkpoint_idx is None or progress['current_step_index'] > checkpoint_idx:
            return progress

        checkpoint_step = flow['steps'][checkpoint_idx]
        hour, minute = map(int, get_effective_checkpoint_time(checkpoint_step, now).split(':'))
        deadline = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < deadline:
            return progress

        eligible_keys = [s['key'] for s in flow['steps'][:checkpoint_idx]]
        done_count = sum(1 for k in eligible_keys if progress['steps_status'].get(k) == 'done')
        ratio = (done_count / len(eligible_keys)) if eligible_keys else 1.0

        for k in eligible_keys:
            if progress['steps_status'].get(k) != 'done':
                progress['steps_status'][k] = 'remind'
        progress['steps_status'][checkpoint_step['key']] = 'done'

        bonus_gold = round(FULL_BONUS_GOLD * ratio)
        bonus_exp = round(FULL_BONUS_EXP * ratio)
        progress['bonus_gold'] = bonus_gold
        progress['bonus_exp'] = bonus_exp

        next_index = checkpoint_idx + 1
        progress['current_step_index'] = next_index
        progress['in_free_time'] = False
        if next_index < len(flow['steps']):
            progress['steps_status'][flow['steps'][next_index]['key']] = 'current'

        self._save_progress(cur, progress)
        if bonus_gold or bonus_exp:
            bonus_result = self._grant_bonus(cur, user_id, bonus_gold, bonus_exp)
            progress['leveled_up'] = bonus_result['leveled_up']
            progress['new_level'] = bonus_result['new_level']

        logger.info(
            f"Routine Checkpoint Passed: User={user_id}, Flow={flow_key}, "
            f"Ratio={ratio:.2f}, Gold={bonus_gold}, Exp={bonus_exp}"
        )
        return progress

    def _serialize_flow(self, flow: RoutineFlow, progress: Dict[str, Any], now: datetime.datetime) -> Dict[str, Any]:
        checkpoint_idx = get_checkpoint_index(flow)
        # 土日は締切時刻が変わりうる(get_effective_checkpoint_time)ため、表示用の
        # checkpoint_timeも「今日」時点の実際の時刻をnowから解決する。
        checkpoint_time = (
            get_effective_checkpoint_time(flow['steps'][checkpoint_idx], now) if checkpoint_idx is not None else None
        )
        steps_out = [
            {
                "key": step['key'],
                "label": step['label'],
                "icon_key": step['icon_key'],
                "is_checkpoint": bool(step['checkpoint_time']),
                "status": progress['steps_status'].get(step['key'], 'locked'),
            }
            for step in flow['steps']
        ]
        return {
            "started": True,
            "title": flow['title'],
            "checkpoint_time": checkpoint_time,
            "current_step_index": progress['current_step_index'],
            "in_free_time": progress['in_free_time'],
            "is_complete": progress['current_step_index'] >= len(flow['steps']),
            "bonus_gold": progress['bonus_gold'],
            "bonus_exp": progress['bonus_exp'],
            # Issue発覚(コードレビュー): チェックポイント通過時のボーナスでレベルアップ
            # しても、quest_service._apply_quest_rewardsのようにleveledUp/newLevelを
            # レスポンスへ含めていなかったため、フロントは常にLEVEL UPトーストを出せず
            # サイレントにDBだけ更新されていた。このフラグは_apply_forced_transitionが
            # チェックポイントを通過した「その呼び出し」でのみprogressへ積まれる
            # (以後の呼び出しは冪等ガードで早期returnするため再度立つことはない)。
            "leveled_up": progress.get('leveled_up', False),
            "new_level": progress.get('new_level'),
            "steps": steps_out,
        }

    def get_today_state(self, user_id: str, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
        with _get_user_balance_lock(user_id):
            with common.get_db_cursor(commit=True) as cur:
                user = cur.execute("SELECT 1 FROM quest_users WHERE user_id=?", (user_id,)).fetchone()
                if not user:
                    raise HTTPException(status_code=404, detail="User not found")

                now = now or datetime.datetime.now(JST)
                date_str = self._today_str(now)
                flows_out: Dict[str, Any] = {}
                for flow_key, flow in ROUTINE_FLOWS.items():
                    if not self._is_flow_started_today(flow, now):
                        flows_out[flow_key] = {"started": False, "title": flow['title']}
                        continue
                    progress = self._get_or_create_progress(cur, user_id, flow_key, flow, date_str)
                    progress = self._apply_forced_transition(cur, user_id, flow_key, flow, progress, now)
                    flows_out[flow_key] = self._serialize_flow(flow, progress, now)

                return {"date": date_str, "flows": flows_out}

    def complete_step(
        self, user_id: str, flow_key: str, step_key: str, now: Optional[datetime.datetime] = None
    ) -> Dict[str, Any]:
        if flow_key not in ROUTINE_FLOWS:
            raise HTTPException(status_code=404, detail="Unknown flow_key")
        flow = ROUTINE_FLOWS[flow_key]

        with _get_user_balance_lock(user_id):
            with common.get_db_cursor(commit=True) as cur:
                user = cur.execute("SELECT 1 FROM quest_users WHERE user_id=?", (user_id,)).fetchone()
                if not user:
                    raise HTTPException(status_code=404, detail="User not found")

                now = now or datetime.datetime.now(JST)
                if not self._is_flow_started_today(flow, now):
                    raise HTTPException(status_code=400, detail="このフローはまだ開始していません")

                date_str = self._today_str(now)
                progress = self._get_or_create_progress(cur, user_id, flow_key, flow, date_str)
                progress = self._apply_forced_transition(cur, user_id, flow_key, flow, progress, now)

                idx = progress['current_step_index']
                if idx >= len(flow['steps']):
                    raise HTTPException(status_code=400, detail="本日のフローは完了しています")

                current_step = flow['steps'][idx]
                if current_step['key'] != step_key:
                    raise HTTPException(status_code=409, detail="表示が古いようです。再読み込みしてください")
                if current_step['checkpoint_time']:
                    raise HTTPException(status_code=400, detail="自由時間は時間になると自動的に次へ進みます")

                progress['steps_status'][step_key] = 'done'
                next_index = idx + 1
                progress['current_step_index'] = next_index
                if next_index < len(flow['steps']):
                    next_step = flow['steps'][next_index]
                    progress['steps_status'][next_step['key']] = 'current'
                    progress['in_free_time'] = bool(next_step['checkpoint_time'])
                self._save_progress(cur, progress)

                # 直後にチェックポイントへ到達し、かつ既に締切時刻を過ぎている場合
                # (例: 出遅れて自由時間に入った瞬間には既に7:50だった)、この場で
                # 通過処理まで済ませ、フロントが追加のポーリングを待たずに済むようにする。
                progress = self._apply_forced_transition(cur, user_id, flow_key, flow, progress, now)

                return self._serialize_flow(flow, progress, now)


routine_service = RoutineService()
