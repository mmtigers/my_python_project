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

from core.utils import get_now_iso
from core.database import get_db_cursor
from core.jp_holidays import is_offday
import config
import game_logic
from core import sound_manager
from routine_data import (
    FULL_BONUS_EXP, FULL_BONUS_GOLD, RoutineFlow, RoutineStep,
    get_checklist_range, get_checkpoint_index, get_effective_checkpoint_time,
    get_flow_set, get_step_reward, is_weekday_skip,
)
from services import switchbot_service
from services.quest.locks import JST, _get_user_balance_lock, logger

# TV自動ON/OFFの対象を智矢個人に限定するための固定user_id(要件確認済み:
# 涼花もrole_childだが、TV操作の対象は智矢のクリアに限定したい)。
TV_UNLOCK_TARGET_USER_ID = 'son'


class RoutineService:
    # weekend_carryover の遡り上限(日)。通常の3〜5連休を賄いつつ、
    # 長期休暇(EXTRA_HOLIDAY_DATES)で無限に繰越されるのを防ぐ。
    _CARRYOVER_MAX_LOOKBACK_DAYS = 7

    def _get_user_row(self, cur, user_id: str):
        """quest_users の行(role含む)を返す。存在しなければ404。

        フローセットの振り分け(子ども用/大人用)に role が要るため、以前の
        `SELECT 1` による存在確認からカラム取得へ変更している。
        """
        user = cur.execute(
            "SELECT user_id, role FROM quest_users WHERE user_id=?", (user_id,)
        ).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return user

    def _flow_set_for(self, cur, user_id: str) -> Dict[str, RoutineFlow]:
        """そのユーザーに出すべきフローセットを返す(存在確認も兼ねる)。"""
        user = self._get_user_row(cur, user_id)
        return get_flow_set(user_id, user['role'])

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

        今日が休日のとき、連休の初日側へ遡り「連休直前の平日」までを対象にする
        (要件: 金曜終わっていれば土日とも不要、土曜終わっていれば日曜だけ不要)。
        土曜なら[金]、日曜なら[土, 金]となり従来の挙動と一致し、月曜が祝日の
        3連休なら[日, 土, 金]、祝日が金曜なら[木]のように連休の長さに追従する。
        平日(学校がある日)は遡らない。

        遡りは最大 `_CARRYOVER_MAX_LOOKBACK_DAYS` 日で打ち切る。年末年始のように
        EXTRA_HOLIDAY_DATES で長い休みを設定した場合に、宿題が延々と「繰越で不要」に
        なり続けないための歯止め。
        """
        if not is_offday(now):
            return []
        dates: list[str] = []
        for delta in range(1, self._CARRYOVER_MAX_LOOKBACK_DAYS + 1):
            day = now - datetime.timedelta(days=delta)
            dates.append(self._today_str(day))
            # 連休直前の平日(学校がある日)まで含めたら打ち切る。
            if not is_offday(day):
                break
        return dates

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

        休日(土日・国民の祝日・家の休み。`core.jp_holidays.is_offday`)に適用される
        ものが2種類 — weekend_skip(例: 休日はhandwash不要)と weekend_carryover
        (例: 宿題は連休直前の平日/連休中に完了していれば以降不要) — 、
        平日に適用されるものが1種類 — weekday_skip(例: パパのキッチン/リビング
        リセットは休日だけ出す) — ある。
        """
        skip_keys: Set[str] = set()
        if not is_offday(now):
            for step in flow['steps']:
                if is_weekday_skip(step):
                    skip_keys.add(step['key'])
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
        # Issue #761 (AUDIT-032): step['checklist'] が True なら get_checklist_range は
        # 必ず範囲を返す、という不変条件は routine_data.py のフロー定義に依存している。
        # routine_data.py は家族の生活動線を記述するデータで変更頻度が高く、この不変条件は
        # 最も変わりやすいファイルに依存している。破れたときに「None is not iterable」の
        # TypeError で 500 になるより、どのフローの定義が壊れているかを名指しして止める。
        checklist_range = get_checklist_range(flow)
        assert checklist_range is not None, (
            f"flow={flow.get('title')!r} は checklist ステップを持つのに "
            "get_checklist_range が None を返した(routine_data.py のフロー定義を確認)"
        )
        start, end = checklist_range
        for i in range(start, end):
            key = flow['steps'][i]['key']
            if statuses.get(key) != 'done':
                statuses[key] = 'current'

    def _row_to_progress(self, row) -> Dict[str, Any]:
        return {
            'id': row['id'],
            # routine_step_events への記録(_record_step_events)に必要な識別子。
            # 呼び出し側も同じ値を持っているが、progressだけを引き回せば記録できる
            # ようにしておくことで、_save_progressの呼び出し箇所が増えたときに
            # 引数の受け渡し漏れで記録が欠落するのを防ぐ。
            'user_id': row['user_id'],
            'flow_key': row['flow_key'],
            'progress_date': row['progress_date'],
            'current_step_index': row['current_step_index'],
            'in_free_time': bool(row['in_free_time']),
            'steps_status': json.loads(row['steps_status']),
            'bonus_gold': row['bonus_gold'],
            'bonus_exp': row['bonus_exp'],
            # 当日「実施せずにdone扱いで始まった」ステップのkey(migrations/0012)。
            # ボーナス按分の母数から除くために使う(_eligible_done_ratio)。
            'skipped_keys': set(json.loads(row['skipped_keys'])),
            # DBに保存済みのsteps_status(差分検出の基準)。steps_statusとは別の
            # dictオブジェクトである必要があるため、同じJSONを2回パースする。
            '_saved_steps_status': json.loads(row['steps_status']),
        }

    def _insert_step_events(
        self, cur, progress: Dict[str, Any], changes: List[Tuple[str, Optional[str], str]],
        source: str, occurred_at: str,
    ) -> None:
        """ステップの状態遷移を routine_step_events へ追記する(migrations/0011)。

        changes は (step_key, from_status, to_status) のリスト。追記専用のため
        既存の読み取り経路には一切影響しない。
        """
        if not changes:
            return
        cur.executemany("""
            INSERT INTO routine_step_events
                (user_id, flow_key, progress_date, step_key, from_status, to_status, source, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (
                progress['user_id'], progress['flow_key'], progress['progress_date'],
                step_key, from_status, to_status, source, occurred_at,
            )
            for step_key, from_status, to_status in changes
        ])

    def _record_step_events(self, cur, progress: Dict[str, Any], source: str, occurred_at: str) -> None:
        """DB保存済みの状態(`_saved_steps_status`)との差分を routine_step_events へ追記する。

        _save_progress からのみ呼ばれる。steps_status を書き換える箇所
        (_toggle_checklist_step・complete_stepの非チェックリスト分岐・
        _apply_forced_transition)は最終的に必ず _save_progress を経由するため、
        ここ1箇所で全ての遷移を捕捉できる。
        """
        saved = progress.get('_saved_steps_status') or {}
        changes = [
            (key, saved.get(key), to_status)
            for key, to_status in progress['steps_status'].items()
            if saved.get(key) != to_status
        ]
        self._insert_step_events(cur, progress, changes, source, occurred_at)

    def _fetch_progress_row(self, cur, user_id: str, flow_key: str, date_str: str):
        return cur.execute(
            "SELECT * FROM routine_progress WHERE user_id=? AND flow_key=? AND progress_date=?",
            (user_id, flow_key, date_str),
        ).fetchone()

    def _build_initial_progress(
        self, cur, user_id: str, flow_key: str, flow: RoutineFlow, date_str: str, now: datetime.datetime
    ) -> dict[str, Any]:
        """当日行がまだ無いときの初期状態を、**DBに書かずに**組み立てる。

        戻り値は `_row_to_progress` と同じ形(ただし `id` は None)。書き込み経路
        (`_get_or_create_progress`)はこれをそのままINSERTし、読み取り専用経路
        (`get_today_state`。Issue #738 / AUDIT-008)は永続化せず表示だけに使う。
        """
        skip_keys = self._resolve_skip_keys(cur, user_id, flow_key, flow, now)
        statuses, current_index = self._empty_statuses(flow, skip_keys)
        # スキップの結果、初期状態から既にチェックポイントに到達している場合
        # (現状は起こらないが、将来handwash以外もweekend_skip化された場合に備える)、
        # complete_stepと同じくin_free_timeも合わせて立てる。
        in_free_time = current_index < len(flow['steps']) and bool(flow['steps'][current_index]['checkpoint_time'])
        return {
            'id': None,
            'user_id': user_id,
            'flow_key': flow_key,
            'progress_date': date_str,
            'current_step_index': current_index,
            'in_free_time': in_free_time,
            'steps_status': statuses,
            'bonus_gold': 0,
            'bonus_exp': 0,
            'skipped_keys': set(skip_keys),
            '_saved_steps_status': {},
        }

    def _read_progress(
        self, cur, user_id: str, flow_key: str, flow: RoutineFlow, date_str: str, now: datetime.datetime
    ) -> dict[str, Any]:
        """当日の進捗を読み取り専用で返す(行が無ければ初期状態を組み立てるだけ)。

        Issue #738 (AUDIT-008): `GET /api/routine/today` は15秒ポーリングで叩かれる
        ため、行のINSERTを伴う `_get_or_create_progress` を使わない。行の作成は
        スケジューラ経由の `process_deadlines`、またはユーザー操作(`complete_step`)が行う。
        """
        row = self._fetch_progress_row(cur, user_id, flow_key, date_str)
        if row:
            return self._row_to_progress(row)
        return self._build_initial_progress(cur, user_id, flow_key, flow, date_str, now)

    def _get_or_create_progress(
        self, cur, user_id: str, flow_key: str, flow: RoutineFlow, date_str: str, now: datetime.datetime
    ) -> Dict[str, Any]:
        row = self._fetch_progress_row(cur, user_id, flow_key, date_str)
        if row:
            return self._row_to_progress(row)

        initial = self._build_initial_progress(cur, user_id, flow_key, flow, date_str, now)
        skip_keys = initial['skipped_keys']

        now_iso = get_now_iso()
        cur.execute("""
            INSERT INTO routine_progress
                (user_id, flow_key, progress_date, current_step_index, in_free_time,
                 steps_status, skipped_keys, bonus_gold, bonus_exp, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
        """, (
            user_id, flow_key, date_str, initial['current_step_index'], int(initial['in_free_time']),
            json.dumps(initial['steps_status'], ensure_ascii=False),
            # 当日やらずにdone扱いで始まった分を行に固定して残す(migrations/0012)。
            # 判定はこの行の作成時に一度だけ行われるため、以降は再計算しない。
            json.dumps(sorted(skip_keys), ensure_ascii=False),
            now_iso, now_iso,
        ))
        row = cur.execute(
            "SELECT * FROM routine_progress WHERE user_id=? AND flow_key=? AND progress_date=?",
            (user_id, flow_key, date_str),
        ).fetchone()
        progress = self._row_to_progress(row)

        # 土日スキップ/繰越により「当日は実施していないのに done 扱い」で始まった
        # ステップを記録しておく(migrations/0011)。これを残さないと、集計側が
        # 「0時ちょうどに宿題を終えた日」として誤って所要時間に混ぜてしまう。
        # 'current'への活性化は進行がそこへ到達しただけで遷移の記録に値しないため
        # 対象にしない。
        self._insert_step_events(
            cur, progress,
            [(s['key'], None, 'done') for s in flow['steps'] if s['key'] in skip_keys],
            'carryover_skip', now.isoformat(),
        )
        return progress

    def _save_progress(
        self, cur, progress: Dict[str, Any], source: str, occurred_at: str
    ) -> None:
        """進捗を保存し、あわせてステップの状態遷移を routine_step_events へ追記する。

        source / occurred_at は呼び出し元が持つ文脈(ユーザー操作か締切超過か、
        およびその時刻)。updated_at に get_now_iso() を使う既存挙動は
        変えず、イベントの時刻だけは注入された now を基準にする
        (テストが now を注入する設計のため、実時刻を使うと検証できなくなる)。
        """
        self._record_step_events(cur, progress, source, occurred_at)
        cur.execute("""
            UPDATE routine_progress
            SET current_step_index=?, in_free_time=?, steps_status=?, bonus_gold=?, bonus_exp=?, updated_at=?
            WHERE id=?
        """, (
            progress['current_step_index'], int(progress['in_free_time']),
            json.dumps(progress['steps_status'], ensure_ascii=False),
            progress['bonus_gold'], progress['bonus_exp'],
            get_now_iso(), progress['id'],
        ))
        # 同一リクエスト内で _save_progress が複数回呼ばれても(complete_step は
        # 保存後にもう一度 _apply_forced_transition を通す)同じ遷移を二重に
        # 記録しないよう、保存済みの状態を更新する。
        progress['_saved_steps_status'] = dict(progress['steps_status'])

    def _eligible_done_ratio(self, flow: RoutineFlow, progress: Dict[str, Any]) -> float:
        """チェックポイントより前の「当日やるべきステップ」のうち、'done'の割合を返す。

        スキップ(weekend_skip / weekend_carryover / weekday_skip)で最初からdone扱いに
        なったステップは、分子だけでなく**分母からも除く**(migrations/0012の
        skipped_keys)。以前はこれらを分母にも分子にも含めていたため、何もしていなくても
        スキップ分だけボーナスが入っていた(例: パパの土日pmは「お仕事」がスキップされる
        ため、一切チェックしなくても 1/3 = 50Gold が入っていた)。

        チェックポイントが無いフローは1.0(満額)とみなす。当日やるべきステップが
        1つも無い場合は0.0を返す — 「達成すべきものが無かった」のであって「満額に
        値する達成をした」わけではないため。以前はゼロ除算回避としてここも1.0を
        返しており、チェックポイントより前が全てスキップされるフローを定義すると
        何もせずに満額が入る穴になっていた。
        """
        checkpoint_idx = get_checkpoint_index(flow)
        if checkpoint_idx is None:
            return 1.0
        skipped_keys = progress.get('skipped_keys') or set()
        eligible_keys = [
            s['key'] for s in flow['steps'][:checkpoint_idx] if s['key'] not in skipped_keys
        ]
        if not eligible_keys:
            return 0.0
        done_count = sum(1 for k in eligible_keys if progress['steps_status'].get(k) == 'done')
        return done_count / len(eligible_keys)

    def _is_checkpoint_path_cleared(self, flow: RoutineFlow, progress: Dict[str, Any]) -> bool:
        """チェックポイントより前の一本道を全て終えているか(スキップ分は達成済み扱い)。

        自由時間に入れるか・TVを解錠してよいかの判定に使う。締切(チェックポイント時刻)
        を過ぎたかどうかは一切見ないため、「時間が来たから自由時間」ではなく
        「終わったから自由時間」になる(要件: 自由時間の時間固定をやめる)。
        """
        checkpoint_idx = get_checkpoint_index(flow)
        if checkpoint_idx is None:
            return True
        return all(
            progress['steps_status'].get(step['key']) == 'done'
            for step in flow['steps'][:checkpoint_idx]
        )

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
            (new_level, new_exp_val, user['gold'] + gold, get_now_iso(), user_id),
        )
        if leveled_up:
            sound_manager.play("level_up")
        return {"leveled_up": leveled_up, "new_level": new_level}

    def _grant_step_reward(self, cur, user_id: str, progress: Dict[str, Any], step: RoutineStep) -> None:
        """ステップ個別の即時報酬(routine_data.RoutineStepのgold/exp)をその場で付与する。

        生活動線そのものだったデイリークエスト(例: ママの「夕食を作る」、家族全員が
        対象だった「就寝ミッション」)をquest_data.QUESTSから「すごろく」へ寄せた分の
        報酬。チェックポイント通過ボーナス(_apply_forced_transition)とは別枠で、
        順番どおり完了報告した「その1回」でのみ加算される(以降そのステップは'done'の
        ままで、シーケンシャルな進行は後戻りしないため二重付与は起きない)。

        （就寝ミッションの移設で訂正）以前は「子ども用フローのステップはgold/expを
        持たないので何もしない」と書いていたが、id=1105の移設で子ども用フローの
        `sleep`「就寝」にもgold/expが付いたため、対象は大人用フローに限らない。
        報酬を持たないステップに対して何もしない点は変わらない(冒頭の早期return)。
        """
        gold, exp = get_step_reward(step)
        if not gold and not exp:
            return
        result = self._grant_bonus(cur, user_id, gold, exp)
        # フロント側で「+50 G」のトーストを出すための、この1レスポンス限りの加算額。
        progress['granted_gold'] = progress.get('granted_gold', 0) + gold
        progress['granted_exp'] = progress.get('granted_exp', 0) + exp
        progress['leveled_up'] = bool(result['leveled_up']) or progress.get('leveled_up', False)
        progress['new_level'] = result['new_level']
        logger.info(
            f"Routine Step Reward: User={user_id}, Step={step['key']}, Gold={gold}, Exp={exp}"
        )

    def _complete_remind_step(
        self, cur, user_id: str, flow_key: str, flow: RoutineFlow,
        progress: Dict[str, Any], step: RoutineStep,
    ) -> None:
        """締切超過で「まだだよ」になった一本道のステップを、後から完了報告する。

        （自由時間の時間固定をやめる変更で新規追加）締切(17:30)を過ぎると寝る準備へ
        進んでしまうが、宿題などを飛ばしたまま自由時間・TVを与えたくない一方で、
        遅れても終わらせたなら報われてほしい(要件)。そこで進行(current_step_index)は
        動かさず、そのステップだけを'done'にする。

        これで一本道が全て埋まったら、チェックポイント(自由時間)も'done'にし、
        締切前に到達した場合と同じくTVを解錠する。既に埋まっていた場合は何もしない
        (呼び出し元が'remind'であることを確認済みのため、ここが二重に走ることはない)。

        チェックポイント通過ボーナス(bonus_gold/bonus_exp)は締切時点の達成率で確定済みで、
        追いつき完了では増えない — 締切の意味をボーナス額だけに残すため(要件)。
        ステップ個別報酬(大人用フローのgold/exp)は実際に作業した分なのでここでも付与する。
        """
        progress['steps_status'][step['key']] = 'done'
        self._grant_step_reward(cur, user_id, progress, step)

        if not self._is_checkpoint_path_cleared(flow, progress):
            return

        checkpoint_idx = get_checkpoint_index(flow)
        if checkpoint_idx is None:
            return
        checkpoint_step = flow['steps'][checkpoint_idx]
        if progress['steps_status'].get(checkpoint_step['key']) == 'done':
            return
        progress['steps_status'][checkpoint_step['key']] = 'done'
        logger.info(
            f"Routine Catch-up Cleared: User={user_id}, Flow={flow_key}, Step={step['key']}"
        )
        if flow_key == 'pm' and user_id == TV_UNLOCK_TARGET_USER_ID and config.TV_PLUG_DEVICE_ID:
            # Issue #737 (AUDIT-007): ここでは発火せず理由を控えるだけにする。
            # 呼び出し元 complete_step がコミット後に trigger_tv_unlock を呼ぶ。
            progress['_tv_unlock_reason'] = "夕方の一本道を締切後に完了(追いつき)"

    def _is_forced_transition_due(
        self, flow: RoutineFlow, progress: dict[str, Any], now: datetime.datetime
    ) -> bool:
        """このフローに「締切超過による強制遷移」を今すぐ適用すべきかを、**読み取りだけで**判定する。

        `_apply_forced_transition` の冒頭のガードをそのまま切り出したもの。Issue #738
        (AUDIT-008)でスケジューラ側(`process_deadlines`)が「書き込みが必要なユーザーだけ」
        を選ぶのに使うため、判定を1箇所に集約して両者がずれないようにしている。
        """
        checkpoint_idx = get_checkpoint_index(flow)
        if checkpoint_idx is None or progress['current_step_index'] > checkpoint_idx:
            return False

        checkpoint_step = flow['steps'][checkpoint_idx]
        # Issue #761 (AUDIT-032): get_checkpoint_index が返したインデックスのステップは
        # checkpoint_time を持つため get_effective_checkpoint_time は None にならない
        # (weekend_checkpoint_time が無ければ checkpoint_time にフォールバックする)。
        # routine_data.py 側の定義変更で破れうるため assert で明示する。
        checkpoint_time = get_effective_checkpoint_time(checkpoint_step, now)
        assert checkpoint_time is not None, (
            f"flow={flow.get('title')!r} step={checkpoint_step.get('key')!r} は "
            "get_checkpoint_index に選ばれたのに checkpoint_time を持たない"
            "(routine_data.py のフロー定義を確認)"
        )
        hour, minute = map(int, checkpoint_time.split(':'))
        deadline = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return now >= deadline

    def _apply_forced_transition(
        self, cur, user_id: str, flow_key: str, flow: RoutineFlow, progress: Dict[str, Any], now: datetime.datetime
    ) -> Dict[str, Any]:
        """チェックポイント(自由時間の終了予定時刻)を過ぎていれば、未完了ステップを
        「まだだよ」に変え、チェックポイントより前のステップの達成率に応じたボーナスを
        付与したうえでチェックポイントの次のステップへ進める。

        current_step_indexがチェックポイントを既に通過していれば何もしない(冪等)ため、
        スケジューラの定期実行(`process_deadlines`)・POST(ステップ完了)のどちらからも
        安全に呼べる。Issue #738 (AUDIT-008)以降、**GET(状態取得)からは呼ばれない**
        （書き込みを伴うため。`get_today_state` のdocstring参照）。

        （自由時間の時間固定をやめる変更で修正）締切を過ぎた時点でチェックポイント
        ステップ(自由時間)を無条件に'done'にはしない。締切までに一本道を終えていなければ
        'remind'(まだだよ)のままにし、TV解錠も行わない — 「宿題を飛ばしても時間が来れば
        自由時間が始まる」状態を無くすため(要件)。寝る準備チェックリストの活性化だけは
        締切で従来どおり行う(晩ごはん・お風呂は宿題の進捗と無関係に進むため)。
        'remind'になった一本道のステップは締切後も完了報告でき(complete_step)、
        全部終えた時点で自由時間が'done'になりTVが解錠される。
        """
        if not self._is_forced_transition_due(flow, progress, now):
            return progress

        # _is_forced_transition_due が True を返した時点でチェックポイントは必ず存在する。
        checkpoint_idx = get_checkpoint_index(flow)
        assert checkpoint_idx is not None
        checkpoint_step = flow['steps'][checkpoint_idx]

        ratio = self._eligible_done_ratio(flow, progress)
        eligible_keys = [s['key'] for s in flow['steps'][:checkpoint_idx]]

        for k in eligible_keys:
            if progress['steps_status'].get(k) != 'done':
                progress['steps_status'][k] = 'remind'
        # 締切までに一本道を終えていれば自由時間は達成、終えていなければ「まだだよ」。
        # 後者はcomplete_stepでの追いつき完了によって'done'へ変わる。
        progress['steps_status'][checkpoint_step['key']] = (
            'done' if self._is_checkpoint_path_cleared(flow, progress) else 'remind'
        )

        bonus_gold = round(FULL_BONUS_GOLD * ratio)
        bonus_exp = round(FULL_BONUS_EXP * ratio)
        progress['bonus_gold'] = bonus_gold
        progress['bonus_exp'] = bonus_exp

        next_index = self._next_active_index(flow, progress['steps_status'], checkpoint_idx + 1)
        progress['current_step_index'] = next_index
        progress['in_free_time'] = False
        self._activate_block(flow, progress['steps_status'], next_index)

        self._save_progress(cur, progress, 'forced_transition', now.isoformat())
        if bonus_gold or bonus_exp:
            bonus_result = self._grant_bonus(cur, user_id, bonus_gold, bonus_exp)
            # 同一リクエスト内で先にステップ個別報酬(_grant_step_reward)によるレベル
            # アップが起きている場合があるため、フラグは上書きせずORで畳む
            # (new_levelは後から付与した側=こちらが最新のレベルになる)。
            progress['leveled_up'] = bonus_result['leveled_up'] or progress.get('leveled_up', False)
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
        # Issue #761 (AUDIT-032): target_step['checklist'] が True なら必ず存在する、という
        # 不変条件を assert で実行可能にする(コメントだけでは routine_data.py の定義変更で
        # 黙って破れる)。詳細は _mark_current_steps 側の同じ assert のコメントを参照。
        checklist_range = get_checklist_range(flow)
        assert checklist_range is not None, (
            f"flow={flow.get('title')!r} は checklist ステップを持つのに "
            "get_checklist_range が None を返した(routine_data.py のフロー定義を確認)"
        )
        start, end = checklist_range
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
            # Issue #737 (AUDIT-007): ここでは発火せず理由を控えるだけにする。
            # とくにこの経路は発火のあとに _save_progress / _apply_forced_transition が
            # 続くため、「TV だけ点いてチェックリストはロールバック」が起きやすかった。
            progress['_tv_unlock_reason'] = "朝の準備チェックリスト全項目達成"
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
                # 大人用フローに寄せたデイリークエスト相当のステップ個別報酬
                # (子ども用フローのステップは常に0)。画面に「+50 G」として出す。
                "gold": get_step_reward(step)[0],
                "exp": get_step_reward(step)[1],
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
            # leveled_upと同じく、ステップ個別報酬を付与した「その1回のレスポンス」
            # でのみ非0になる(GET /todayは付与を行わないため常に0)。
            "granted_gold": progress.get('granted_gold', 0),
            "granted_exp": progress.get('granted_exp', 0),
            "steps": steps_out,
        }

    def get_today_state(self, user_id: str, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
        """当日の状態を**読み取り専用で**返す(Issue #738 / AUDIT-008)。

        このエンドポイント(`GET /api/routine/today`)は全端末が15秒間隔でポーリングする。
        以前はこの中で `_get_or_create_progress`(当日行のINSERT)と
        `_apply_forced_transition`(締切超過時のステップ書き換え・ボーナス付与)を
        行っていたため、

        - `GET` が安全(safe)でなくなる(プリフェッチ・React Query のリトライ・
          `refetchOnWindowFocus` がそのまま報酬付与のトリガーになる)
        - 端末の台数×4回/分だけ `_get_user_balance_lock` を取り合い、その間
          同じユーザーのクエスト完了・承認・購入がブロックされる
        - 状態が変わらない大多数のポーリングでも書き込みトランザクションを開き、
          SDカードへのfsyncが走る
        - 締切超過の強制遷移が「誰かが画面を開いていること」に依存する

        という問題があった。現在、締切超過の処理は `process_deadlines`
        (スケジューラが `monitors/routine_deadline_job.py` 経由で60秒ごとに呼ぶ)が
        担う。ここではロックも書き込みトランザクションも取らず、当日行がまだ
        無ければ初期状態を組み立てて返すだけ(永続化しない)。

        結果として、締切通過の反映はスケジューラの実行間隔(最大60秒)だけ遅れうる。
        ユーザーがステップを完了報告した時点では `complete_step` が同じ
        `_apply_forced_transition` を通すため、操作に対する応答は従来どおり即時。
        """
        now = now or datetime.datetime.now(JST)
        date_str = self._today_str(now)
        flows_out: Dict[str, Any] = {}
        with get_db_cursor() as cur:  # commit=False(読み取り専用)
            flows = self._flow_set_for(cur, user_id)
            for flow_key, flow in flows.items():
                if not self._is_flow_started_today(flow, now):
                    flows_out[flow_key] = {"started": False, "title": flow['title']}
                    continue
                progress = self._read_progress(cur, user_id, flow_key, flow, date_str, now)
                flows_out[flow_key] = self._serialize_flow(flow, progress, now)

        return {"date": date_str, "flows": flows_out}

    def _has_pending_deadline_work(
        self, cur, user_id: str, flows: dict[str, RoutineFlow], date_str: str, now: datetime.datetime
    ) -> bool:
        """このユーザーについて、書き込みを伴う締切処理が必要かを読み取りだけで判定する。

        「当日行がまだ無い」か「締切を過ぎているのに強制遷移が未適用」のどちらかが
        あれば True。大多数の実行はここで False になり、残高ロックも書き込み
        トランザクションも取らずに済む(Issue #738 の「書き込みの無駄」への対応)。
        """
        for flow_key, flow in flows.items():
            if not self._is_flow_started_today(flow, now):
                continue
            row = self._fetch_progress_row(cur, user_id, flow_key, date_str)
            if row is None:
                return True
            if self._is_forced_transition_due(flow, self._row_to_progress(row), now):
                return True
        return False

    def _process_user_deadlines(
        self, user_id: str, role: str | None, date_str: str, now: datetime.datetime
    ) -> int:
        """1ユーザー分の締切処理を行い、強制遷移を適用したフロー数を返す。"""
        flows = get_flow_set(user_id, role)

        with get_db_cursor() as cur:  # まず読み取りだけで必要性を判定する
            if not self._has_pending_deadline_work(cur, user_id, flows, date_str, now):
                return 0

        applied = 0
        # 書き込みが要ると分かったときだけ、クエスト完了・承認・購入と同じ
        # ユーザー残高ロックを取る(_grant_bonus が quest_users を read-modify-write するため)。
        with _get_user_balance_lock(user_id):
            with get_db_cursor(commit=True) as cur:
                for flow_key, flow in flows.items():
                    if not self._is_flow_started_today(flow, now):
                        continue
                    progress = self._get_or_create_progress(cur, user_id, flow_key, flow, date_str, now)
                    before_index = progress['current_step_index']
                    progress = self._apply_forced_transition(cur, user_id, flow_key, flow, progress, now)
                    if progress['current_step_index'] != before_index:
                        applied += 1
        return applied

    def process_deadlines(self, now: datetime.datetime | None = None) -> dict[str, Any]:
        """全ユーザーの締切超過(チェックポイント通過)を適用する(Issue #738 / AUDIT-008)。

        `scheduler_boot.TASKS` に登録した `monitors/routine_deadline_job.py` が
        60秒ごとに `POST /api/routine/deadlines/process` を叩き、**unified_server の
        プロセス内で**この関数が動く。スケジューラは別プロセスのため、
        `quest_users` を直接書き換えず必ずHTTP API を経由すること
        (CLAUDE.md「並行制御は単一プロセス前提」/ `reset_game.py` と同じ方針)。

        `now` はテスト用の注入専用で、APIからは渡せない(クライアントが締切判定の
        基準時刻を操作できてしまうため)。

        1ユーザーの失敗が他のユーザーの処理を巻き込まないよう、例外はユーザー単位で
        捕捉してログに残し、処理を続行する(スケジューラのタスク自体は落とさない)。
        """
        now = now or datetime.datetime.now(JST)
        date_str = self._today_str(now)

        with get_db_cursor() as cur:
            users = [
                (row['user_id'], row['role'])
                for row in cur.execute("SELECT user_id, role FROM quest_users").fetchall()
            ]

        processed = 0
        failed = 0
        transitions = 0
        for user_id, role in users:
            try:
                transitions += self._process_user_deadlines(user_id, role, date_str, now)
                processed += 1
            except Exception:  # noqa: BLE001 — 無人実行なので、失敗理由に関わらず
                # 他のユーザーの締切処理まで巻き込まない(sync_strict.py と同じ方針)。
                failed += 1
                logger.exception(f"Routine Deadline: user={user_id} の締切処理に失敗しました")

        if transitions or failed:
            logger.info(
                f"Routine Deadline Processed: Date={date_str}, Users={processed}, "
                f"Transitions={transitions}, Failed={failed}"
            )
        return {
            "date": date_str,
            "processed_users": processed,
            "failed_users": failed,
            "transitions": transitions,
        }

    def complete_step(
        self, user_id: str, flow_key: str, step_key: str, now: Optional[datetime.datetime] = None
    ) -> Dict[str, Any]:
        # Issue #737 (AUDIT-007): TV解錠(SwitchBot API 経由の fire-and-forget な物理的
        # 副作用)はトランザクションのコミット後に起動する。以前は with ブロック内
        # (コミット前)で直接呼んでいたため、コミットが失敗(database is locked のリトライ
        # 超過・ディスクフル等)してロールバックされても TV だけが点きうる状態だった。
        # とくにチェックリスト経路では、ロールバック後に UI が未達成を表示して
        # もう一度チェックでき、was_all_done は「今回の DB 状態」から計算されるため
        # **TV 解錠が二度発火する**。approval_service._process_approve_quest_locked
        # (および #544 の inventory_service)と同じパターンへ揃える。
        tv_unlock_reason: str | None = None
        with _get_user_balance_lock(user_id):
            with get_db_cursor(commit=True) as cur:
                # フローの内容はユーザー(子ども/パパ/ママ)によって異なるため、
                # flow_keyの検証もユーザーを解決してから行う。
                flows = self._flow_set_for(cur, user_id)
                if flow_key not in flows:
                    raise HTTPException(status_code=404, detail="Unknown flow_key")
                flow = flows[flow_key]

                now = now or datetime.datetime.now(JST)
                if not self._is_flow_started_today(flow, now):
                    raise HTTPException(status_code=400, detail="このフローはまだ開始していません")

                date_str = self._today_str(now)
                progress = self._get_or_create_progress(cur, user_id, flow_key, flow, date_str, now)
                progress = self._apply_forced_transition(cur, user_id, flow_key, flow, progress, now)

                idx = progress['current_step_index']
                target_step = next((s for s in flow['steps'] if s['key'] == step_key), None)
                if target_step is None:
                    raise HTTPException(status_code=404, detail="Unknown step_key")

                # 締切超過で「まだだよ」になった一本道のステップは、進行が先へ進んだ
                # 後でも(フローを最後まで終えた後でも)追いつきで完了報告できる。
                # 完了しているか判定してから通常の完了経路のガードに入る。
                # チェックポイント(自由時間)自身も締切で'remind'になりうるが、これは
                # 一本道を全部終えた結果としてのみ'done'になるべきもので、直接完了報告
                # させてはいけない(下の逐次分岐の400で従来どおり弾く)。
                is_catch_up = (
                    not target_step['checklist']
                    and not target_step['checkpoint_time']
                    and progress['steps_status'].get(step_key) == 'remind'
                )
                if idx >= len(flow['steps']) and not is_catch_up:
                    raise HTTPException(status_code=400, detail="本日のフローは完了しています")

                if is_catch_up:
                    self._complete_remind_step(cur, user_id, flow_key, flow, progress, target_step)
                elif target_step['checklist']:
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
                    self._grant_step_reward(cur, user_id, progress, current_step)
                    next_index = self._next_active_index(flow, progress['steps_status'], idx + 1)
                    progress['current_step_index'] = next_index
                    self._activate_block(flow, progress['steps_status'], next_index)
                    if next_index < len(flow['steps']):
                        entered_free_time = bool(flow['steps'][next_index]['checkpoint_time'])
                        progress['in_free_time'] = entered_free_time
                        # （夕方フリータイムでTV解錠を追加）宿題・明日の準備まで完了して
                        # 自由時間(pmのfreeステップ)に到達した瞬間、朝の準備チェックリスト
                        # 全達成時と同じTV電源ON処理を呼ぶ。締切(17:30)超過による強制遷移
                        # (_apply_forced_transition)経由でチェックリストへ直接進んだ場合は
                        # ここを通らないため発火しない。対象は智矢(TV_UNLOCK_TARGET_USER_ID)
                        # のみで、涼花(role_childだが対象外)がクリアしても発火しない。
                        if (
                            entered_free_time
                            and flow_key == 'pm'
                            and user_id == TV_UNLOCK_TARGET_USER_ID
                            and config.TV_PLUG_DEVICE_ID
                        ):
                            # Issue #737 (AUDIT-007): コミット後に発火する(下記参照)
                            progress['_tv_unlock_reason'] = "夕方の自由時間開始(宿題・明日の準備完了)"
                self._save_progress(cur, progress, 'user', now.isoformat())

                # 直後にチェックポイントへ到達し、かつ既に締切時刻を過ぎている場合
                # (例: 出遅れて自由時間に入った瞬間には既に7:50だった)、この場で
                # 通過処理まで済ませ、フロントが追加のポーリングを待たずに済むようにする。
                progress = self._apply_forced_transition(cur, user_id, flow_key, flow, progress, now)

                result = self._serialize_flow(flow, progress, now)
                # このリクエスト限りのフラグ(granted_gold / leveled_up と同じ慣習)。
                # 取り出したら消して、progress が再利用されても持ち越さないようにする。
                tv_unlock_reason = progress.pop('_tv_unlock_reason', None)

        # ここはコミット済み・ロック解放済み。ここで初めて不可逆な副作用を起こす。
        if tv_unlock_reason:
            switchbot_service.trigger_tv_unlock(tv_unlock_reason)
        return result


routine_service = RoutineService()
