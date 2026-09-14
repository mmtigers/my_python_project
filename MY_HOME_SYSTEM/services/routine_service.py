"""デイリールーティン(すごろく形式の生活導線UI)のサービス層。

routers/routine_router.py はパース・検証のみを行い、ロジックはここに委譲する
(CLAUDE.mdのレイヤリング規約)。quest_users(gold/exp/level)への書き込みを伴うため、
services/quest/locks.py の user balance lock を quest_service と共用し、
クエスト完了/承認と同一ユーザーへの並行更新によるlost updateを防ぐ。
"""
import datetime
import json
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import HTTPException

import common
import config
import game_logic
from core import sound_manager
from routine_data import (
    FULL_BONUS_EXP, FULL_BONUS_GOLD, ROUTINE_FLOWS, WEEKEND_DAYS, RoutineFlow,
    get_checklist_range, get_checkpoint_index, get_effective_checkpoint_time,
)
from services import switchbot_service
from services.quest.locks import JST, _get_user_balance_lock, logger

# TV自動ON/OFFの対象を智矢個人に限定するための固定user_id(要件確認済み:
# 涼花もrole_childだが、TV操作の対象は智矢のクリアに限定したい)。
TV_UNLOCK_TARGET_USER_ID = 'son'


class RoutineService:
    def _today_str(self, now: datetime.datetime) -> str:
        return now.strftime('%Y-%m-%d')

    def _is_flow_started_today(self, flow: RoutineFlow, now: datetime.datetime) -> bool:
        if now.weekday() not in flow['day_of_week']:
            return False
        hour, minute = map(int, flow['start_trigger_time'].split(':'))
        trigger = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return now >= trigger

    def _carryover_lookback_dates(self, now: datetime.datetime) -> List[str]:
        """weekend_carryoverステップについて、完了済みか確認すべき過去日付を返す。

        土曜は金曜のみ、日曜は金曜・土曜の両方を遡る(要件: 金曜終わっていれば
        土日とも不要、土曜終わっていれば日曜だけ不要)。平日は遡らない。
        """
        weekday = now.weekday()
        if weekday == 5:  # 土曜
            return [self._today_str(now - datetime.timedelta(days=1))]
        if weekday == 6:  # 日曜
            return [
                self._today_str(now - datetime.timedelta(days=1)),
                self._today_str(now - datetime.timedelta(days=2)),
            ]
        return []

    def _was_done_on_any_date(self, cur, user_id: str, flow_key: str, key: str, dates: List[str]) -> bool:
        if not dates:
            return False
        placeholders = ','.join('?' for _ in dates)
        rows = cur.execute(
            f"SELECT steps_status FROM routine_progress "  # nosec B608
            f"WHERE user_id=? AND flow_key=? AND progress_date IN ({placeholders})",
            (user_id, flow_key, *dates),
        ).fetchall()
        return any(json.loads(row['steps_status']).get(key) == 'done' for row in rows)

    def _resolve_skip_keys(self, cur, user_id: str, flow_key: str, flow: RoutineFlow, now: datetime.datetime) -> Set[str]:
        """今日スキップ(達成済み扱い)すべきステップのkey集合を返す。

        weekend_skip(例: 土日はhandwash不要)とweekend_carryover(例: 宿題は
        金曜/土曜に完了していれば以降不要)の2種類があり、いずれも平日には適用しない。
        """
        skip_keys: Set[str] = set()
        if now.weekday() not in WEEKEND_DAYS:
            return skip_keys
        lookback_dates = self._carryover_lookback_dates(now)
        for step in flow['steps']:
            if step['weekend_skip']:
                skip_keys.add(step['key'])
            elif step['weekend_carryover'] and self._was_done_on_any_date(cur, user_id, flow_key, step['key'], lookback_dates):
                skip_keys.add(step['key'])
        return skip_keys

    def _empty_statuses(self, flow: RoutineFlow, skip_keys: Set[str]) -> Tuple[Dict[str, str], int]:
        """スキップ分を'done'扱いにしたうえで初期状態を組み立てる。

        スキップされていない最初のステップを`_activate_block`で'current'にする
        (そのステップがchecklist=Trueなグループの一員なら、グループ全体を同時に
        'current'にする。例: 朝の準備5項目はフロー先頭のチェックリストなので、
        フロー開始時点でグループ全体が一括で着手可能になる)。

        戻り値は(steps_status, current_step_index)。全ステップがスキップ済みの場合の
        current_step_indexはlen(flow['steps'])(=フロー完了扱い)になる。
        """
        statuses: Dict[str, str] = {
            step['key']: ('done' if step['key'] in skip_keys else 'locked')
            for step in flow['steps']
        }
        current_index = self._next_active_index(flow, statuses, 0)
        self._activate_block(flow, statuses, current_index)
        return statuses, current_index

    def _next_active_index(self, flow: RoutineFlow, statuses: Dict[str, str], start_index: int) -> int:
        """start_index以降で、スキップ済み('done'が既に立っている)ステップを飛ばした
        最初のステップのインデックスを返す(無ければlen(flow['steps']))。

        現状はチェックポイント以前のステップしかweekend_skip/weekend_carryoverの
        対象にしていないが、以降のステップが対象になった場合にも安全なようにする。
        """
        idx = start_index
        while idx < len(flow['steps']) and statuses.get(flow['steps'][idx]['key']) == 'done':
            idx += 1
        return idx

    def _activate_block(self, flow: RoutineFlow, statuses: Dict[str, str], index: int) -> None:
        """indexのステップを'current'にする(進行がそこに到達した合図)。

        indexがchecklist=Trueなグループの一員なら、グループ全体(未doneの項目のみ)を
        同時に'current'にする(要件: チェックリストは順不同で全項目同時に着手できる)。
        フロー完了(index >= len(flow['steps']))なら何もしない。
        """
        if index >= len(flow['steps']):
            return
        step = flow['steps'][index]
        if not step['checklist']:
            statuses[step['key']] = 'current'
            return
        start, end = get_checklist_range(flow)
        for i in range(start, end):
            key = flow['steps'][i]['key']
            if statuses.get(key) != 'done':
                statuses[key] = 'current'

    def _row_to_progress(self, row) -> Dict[str, Any]:
        return {
            'id': row['id'],
            'current_step_index': row['current_step_index'],
            'in_free_time': bool(row['in_free_time']),
            'steps_status': json.loads(row['steps_status']),
            'bonus_gold': row['bonus_gold'],
            'bonus_exp': row['bonus_exp'],
        }

    def _get_or_create_progress(
        self, cur, user_id: str, flow_key: str, flow: RoutineFlow, date_str: str, now: datetime.datetime
    ) -> Dict[str, Any]:
        row = cur.execute(
            "SELECT * FROM routine_progress WHERE user_id=? AND flow_key=? AND progress_date=?",
            (user_id, flow_key, date_str),
        ).fetchone()
        if row:
            return self._row_to_progress(row)

        skip_keys = self._resolve_skip_keys(cur, user_id, flow_key, flow, now)
        statuses, current_index = self._empty_statuses(flow, skip_keys)
        # スキップの結果、初期状態から既にチェックポイントに到達している場合
        # (現状は起こらないが、将来handwash以外もweekend_skip化された場合に備える)、
        # complete_stepと同じくin_free_timeも合わせて立てる。
        in_free_time = current_index < len(flow['steps']) and bool(flow['steps'][current_index]['checkpoint_time'])

        now_iso = common.get_now_iso()
        cur.execute("""
            INSERT INTO routine_progress
                (user_id, flow_key, progress_date, current_step_index, in_free_time,
                 steps_status, bonus_gold, bonus_exp, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
        """, (
            user_id, flow_key, date_str, current_index, int(in_free_time),
            json.dumps(statuses, ensure_ascii=False),
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

    def _eligible_done_ratio(self, flow: RoutineFlow, progress: Dict[str, Any]) -> float:
        """チェックポイントより前の全ステップのうち、'done'の割合(0.0〜1.0)を返す。

        チェックポイントが無いフロー、またはチェックポイントより前にステップが
        無いフローは常に1.0(満額)とみなす(ゼロ除算回避)。
        """
        checkpoint_idx = get_checkpoint_index(flow)
        if checkpoint_idx is None:
            return 1.0
        eligible_keys = [s['key'] for s in flow['steps'][:checkpoint_idx]]
        if not eligible_keys:
            return 1.0
        done_count = sum(1 for k in eligible_keys if progress['steps_status'].get(k) == 'done')
        return done_count / len(eligible_keys)

    def _compute_bonus_preview(self, flow: RoutineFlow, progress: Dict[str, Any]) -> Tuple[int, int]:
        """「今チェックしている分」を基準にしたボーナス見込み額(gold, exp)を返す。

        チェックポイント通過前は「今チェックポイントを迎えたらいくらもらえるか」の
        ライブプレビュー、通過後は_apply_forced_transitionが確定させたratioと同じ値に
        自然に収束する(いずれも同じ_eligible_done_ratioを使うため)。実際の付与は
        _apply_forced_transitionでのみ行われ、この関数はDBを変更しない(表示専用)。
        """
        ratio = self._eligible_done_ratio(flow, progress)
        return round(FULL_BONUS_GOLD * ratio), round(FULL_BONUS_EXP * ratio)

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

        ratio = self._eligible_done_ratio(flow, progress)
        eligible_keys = [s['key'] for s in flow['steps'][:checkpoint_idx]]

        for k in eligible_keys:
            if progress['steps_status'].get(k) != 'done':
                progress['steps_status'][k] = 'remind'
        progress['steps_status'][checkpoint_step['key']] = 'done'

        bonus_gold = round(FULL_BONUS_GOLD * ratio)
        bonus_exp = round(FULL_BONUS_EXP * ratio)
        progress['bonus_gold'] = bonus_gold
        progress['bonus_exp'] = bonus_exp

        next_index = self._next_active_index(flow, progress['steps_status'], checkpoint_idx + 1)
        progress['current_step_index'] = next_index
        progress['in_free_time'] = False
        self._activate_block(flow, progress['steps_status'], next_index)

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

    def _toggle_checklist_step(
        self, flow: RoutineFlow, progress: Dict[str, Any], target_step: Dict[str, Any],
        flow_key: str, user_id: str,
    ) -> None:
        """checklist=Trueなステップを順不同でチェック/チェック解除する(要件: 朝の準備・
        寝る準備は好きな順にチェックでき、間違えたら取り消せるようにしたい)。

        チェックリストのブロック(get_checklist_range)は、amではフロー先頭(その直後に
        出発チェックポイントが続く)、pmではチェックポイント通過後(その直後は単に
        「就寝」)に置かれており、いずれも「ブロックの直後のステップ」がブロックの
        境界(boundary)になる。境界を既に過ぎている(current_step_indexが境界より後、
        =amなら出発済み・pmなら就寝を完了済み)場合は変更を拒否する。ブロックが
        まだ'locked'(シーケンシャルな進行がまだそこに到達していない)状態での
        トグルも同様に拒否する。
        チェックリスト全項目が'done'になった/でなくなったタイミングで、フローの
        現在地(current_step_index)・in_free_timeをブロックの前後にまとめて進める/戻す。

        （毎朝ミッション統合で追加）amフローのチェックリスト(朝の準備)が新たに
        全項目達成状態へ遷移した瞬間(既に全達成だった状態からのトグルでは発火しない)、
        対象ユーザーが智矢(TV_UNLOCK_TARGET_USER_ID)であれば、旧quest_id=1100
        「毎朝ミッション」(廃止済み、routine_data.py参照)の承認時と同じTV電源ON処理
        (switchbot_service.trigger_tv_unlock)を呼ぶ。涼花も子供(role_child)だが、
        TV操作の対象は智矢のクリアに限定する(要件確認済み)。pmの寝る準備チェックリストは
        対象外。
        """
        start, end = get_checklist_range(flow)  # target_step['checklist']がTrueなので必ず存在する
        if progress['current_step_index'] > end:
            raise HTTPException(status_code=400, detail="すでに次のステップに進んでいるため変更できません")

        key = target_step['key']
        if progress['steps_status'].get(key) == 'locked':
            raise HTTPException(status_code=400, detail="まだこのステップには進んでいません")

        checklist_keys = [flow['steps'][i]['key'] for i in range(start, end)]
        was_all_done = all(progress['steps_status'].get(k) == 'done' for k in checklist_keys)

        # 'current'=未チェック(いつでもチェック可能)、'done'=チェック済み、の2値をトグルする。
        progress['steps_status'][key] = 'current' if progress['steps_status'].get(key) == 'done' else 'done'

        all_done = all(progress['steps_status'].get(k) == 'done' for k in checklist_keys)
        if (
            all_done
            and not was_all_done
            and flow_key == 'am'
            and user_id == TV_UNLOCK_TARGET_USER_ID
            and config.TV_PLUG_DEVICE_ID
        ):
            switchbot_service.trigger_tv_unlock("朝の準備チェックリスト全項目達成")
        if all_done:
            progress['current_step_index'] = end
            if end < len(flow['steps']):
                boundary_step = flow['steps'][end]
                progress['steps_status'][boundary_step['key']] = 'current'
                progress['in_free_time'] = bool(boundary_step['checkpoint_time'])
            else:
                progress['in_free_time'] = False
        else:
            progress['current_step_index'] = start
            progress['in_free_time'] = False
            if end < len(flow['steps']):
                progress['steps_status'][flow['steps'][end]['key']] = 'locked'

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
                "is_checklist": step['checklist'],
                "status": progress['steps_status'].get(step['key'], 'locked'),
            }
            for step in flow['steps']
        ]
        preview_bonus_gold, _preview_bonus_exp = self._compute_bonus_preview(flow, progress)
        return {
            "started": True,
            "title": flow['title'],
            "checkpoint_time": checkpoint_time,
            "current_step_index": progress['current_step_index'],
            "in_free_time": progress['in_free_time'],
            "is_complete": progress['current_step_index'] >= len(flow['steps']),
            "bonus_gold": progress['bonus_gold'],
            "bonus_exp": progress['bonus_exp'],
            # チェックリスト(例: 朝の準備)を1つチェックするたびに増えていく、出発時に
            # もらえるゴールドの見込み額(要件: チェックした分が画面でわかるようにしたい)。
            # チェックポイント通過前はライブプレビュー、通過後はbonus_goldと同じ値になる。
            "preview_bonus_gold": preview_bonus_gold,
            "bonus_full_gold": FULL_BONUS_GOLD,
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
                    progress = self._get_or_create_progress(cur, user_id, flow_key, flow, date_str, now)
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
                progress = self._get_or_create_progress(cur, user_id, flow_key, flow, date_str, now)
                progress = self._apply_forced_transition(cur, user_id, flow_key, flow, progress, now)

                idx = progress['current_step_index']
                if idx >= len(flow['steps']):
                    raise HTTPException(status_code=400, detail="本日のフローは完了しています")

                target_step = next((s for s in flow['steps'] if s['key'] == step_key), None)
                if target_step is None:
                    raise HTTPException(status_code=404, detail="Unknown step_key")

                if target_step['checklist']:
                    # チェックリストのステップは、そのブロックに進行が到達していれば
                    # (='locked'でなければ)順不同でチェック/チェック解除できる
                    # (要件: 朝の準備・寝る準備は好きな順で良い)。
                    self._toggle_checklist_step(flow, progress, target_step, flow_key, user_id)
                else:
                    current_step = flow['steps'][idx]
                    if current_step['key'] != step_key:
                        raise HTTPException(status_code=409, detail="表示が古いようです。再読み込みしてください")
                    if current_step['checkpoint_time']:
                        raise HTTPException(status_code=400, detail="自由時間は時間になると自動的に次へ進みます")

                    progress['steps_status'][step_key] = 'done'
                    next_index = self._next_active_index(flow, progress['steps_status'], idx + 1)
                    progress['current_step_index'] = next_index
                    self._activate_block(flow, progress['steps_status'], next_index)
                    if next_index < len(flow['steps']):
                        entered_free_time = bool(flow['steps'][next_index]['checkpoint_time'])
                        progress['in_free_time'] = entered_free_time
                        # （夕方フリータイムでTV解錠を追加）宿題・明日の準備まで完了して
                        # 自由時間(pmのfreeステップ)に到達した瞬間、朝の準備チェックリスト
                        # 全達成時と同じTV電源ON処理を呼ぶ。締切(18:00)超過による強制遷移
                        # (_apply_forced_transition)経由でチェックリストへ直接進んだ場合は
                        # ここを通らないため発火しない。対象は智矢(TV_UNLOCK_TARGET_USER_ID)
                        # のみで、涼花(role_childだが対象外)がクリアしても発火しない。
                        if (
                            entered_free_time
                            and flow_key == 'pm'
                            and user_id == TV_UNLOCK_TARGET_USER_ID
                            and config.TV_PLUG_DEVICE_ID
                        ):
                            switchbot_service.trigger_tv_unlock("夕方の自由時間開始(宿題・明日の準備完了)")
                self._save_progress(cur, progress)

                # 直後にチェックポイントへ到達し、かつ既に締切時刻を過ぎている場合
                # (例: 出遅れて自由時間に入った瞬間には既に7:50だった)、この場で
                # 通過処理まで済ませ、フロントが追加のポーリングを待たずに済むようにする。
                progress = self._apply_forced_transition(cur, user_id, flow_key, flow, progress, now)

                return self._serialize_flow(flow, progress, now)


routine_service = RoutineService()
