# MY_HOME_SYSTEM/tests/test_routine_service.py
"""
services/routine_service.py のテスト。

quest_service.py の既存テストと異なり、本サービスの主要な分岐(フローの
開始判定・チェックポイント通過判定)は現在時刻に依存するため、
datetime.datetime.now() をmonkeypatchせず、公開メソッドに now を明示的に
注入できる設計にしている(quest_service._is_quest_currently_activeの
既存パターンに合わせた)。テストは常に2024-01-01(月曜日, JST)を基準にする。
"""
import datetime
from unittest.mock import MagicMock

import pytest

import common
import config
from services import switchbot_service
from services.routine_service import routine_service

JST = datetime.timezone(datetime.timedelta(hours=9), 'JST')
MONDAY = datetime.datetime(2024, 1, 1, tzinfo=JST)  # 2024-01-01は月曜日


def _seed_user(user_id='daughter', gold=0, exp=0, level=1, role='role_child'):
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, 'テスト子', 'Novice', level, exp, gold, role),
        )


def _at(hour, minute):
    return MONDAY.replace(hour=hour, minute=minute)


def _friday_at(hour, minute):
    return (MONDAY + datetime.timedelta(days=4)).replace(hour=hour, minute=minute)  # 2024-01-05は金曜日


def _saturday_at(hour, minute):
    return (MONDAY + datetime.timedelta(days=5)).replace(hour=hour, minute=minute)  # 2024-01-06は土曜日


def _sunday_at(hour, minute):
    return (MONDAY + datetime.timedelta(days=6)).replace(hour=hour, minute=minute)  # 2024-01-07は日曜日


class TestFlowStartGating:
    def test_am_flow_not_started_before_5am(self, isolated_db):
        _seed_user()
        state = routine_service.get_today_state('daughter', now=_at(4, 59))
        assert state['flows']['am']['started'] is False

    def test_am_flow_started_after_5am(self, isolated_db):
        """朝の準備5項目(チェックリスト)は順番を問わず全て最初から'current'
        (チェック可能)になり、自由時間・出発だけが'locked'のまま(要件確認済み)。"""
        _seed_user()
        state = routine_service.get_today_state('daughter', now=_at(5, 0))
        am = state['flows']['am']
        assert am['started'] is True
        checklist_keys = ['meal', 'clothes', 'wash', 'teeth', 'toilet']
        statuses = {s['key']: s['status'] for s in am['steps']}
        assert [s['key'] for s in am['steps'][:5]] == checklist_keys
        assert all(statuses[k] == 'current' for k in checklist_keys)
        assert statuses['free'] == 'locked'
        assert statuses['leave'] == 'locked'
        assert am['current_step_index'] == 0

    def test_am_flow_started_on_weekend(self, isolated_db):
        """土日も平日と同じ05:00開始トリガーでフローが始まる(要件: 平日と揃える)。"""
        _seed_user()
        state = routine_service.get_today_state('daughter', now=_saturday_at(6, 0))
        am = state['flows']['am']
        assert am['started'] is True
        assert am['steps'][0]['status'] == 'current'

    def test_pm_flow_not_started_before_14(self, isolated_db):
        _seed_user()
        state = routine_service.get_today_state('daughter', now=_at(13, 59))
        assert state['flows']['pm']['started'] is False

    def test_pm_flow_started_after_14(self, isolated_db):
        _seed_user()
        state = routine_service.get_today_state('daughter', now=_at(14, 0))
        assert state['flows']['pm']['started'] is True
        assert state['flows']['pm']['steps'][0]['status'] == 'current'

    def test_pm_flow_started_after_14_on_weekend(self, isolated_db):
        """夕方(pm)の開始トリガーは土日も平日と同じ14:00(要件確認済み)。

        ただし土日は手洗い・うがい(steps[0])をスキップしておやつ休憩(steps[1])から
        始まる(要件: 休日のPMはおやつ休憩からスタート)。
        """
        _seed_user()
        state = routine_service.get_today_state('daughter', now=_saturday_at(14, 0))
        assert state['flows']['pm']['started'] is True
        assert state['flows']['pm']['steps'][0]['key'] == 'handwash'
        assert state['flows']['pm']['steps'][0]['status'] == 'done'
        assert state['flows']['pm']['steps'][1]['key'] == 'snack'
        assert state['flows']['pm']['steps'][1]['status'] == 'current'
        assert state['flows']['pm']['current_step_index'] == 1

    def test_unknown_user_returns_404(self, isolated_db):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            routine_service.get_today_state('nobody', now=_at(6, 0))
        assert exc_info.value.status_code == 404


class TestStepCompletion:
    def test_completing_checklist_step_in_any_order(self, isolated_db):
        """朝の準備は順不同でチェックできる(要件確認済み)。表示順(meal→clothes→
        wash→teeth→toilet)を無視して、後ろの項目から先にチェックしても良い。"""
        _seed_user()
        state = routine_service.complete_step('daughter', 'am', 'toilet', now=_at(6, 0))
        statuses = {s['key']: s['status'] for s in state['steps']}
        assert statuses['toilet'] == 'done'
        assert statuses['meal'] == 'current'  # 他の項目は引き続きチェック可能なまま
        assert state['current_step_index'] == 0  # チェックリストフェーズの目印は不変

        state = routine_service.complete_step('daughter', 'am', 'meal', now=_at(6, 1))
        statuses = {s['key']: s['status'] for s in state['steps']}
        assert statuses['meal'] == 'done'
        assert statuses['toilet'] == 'done'

    def test_toggling_checklist_step_off_again(self, isolated_db):
        """一度チェックした項目は再タップで取り消せる(要件確認済み)。"""
        _seed_user()
        routine_service.complete_step('daughter', 'am', 'wash', now=_at(6, 0))
        state = routine_service.complete_step('daughter', 'am', 'wash', now=_at(6, 1))
        statuses = {s['key']: s['status'] for s in state['steps']}
        assert statuses['wash'] == 'current'

    def test_completing_all_checklist_items_advances_to_free_time(self, isolated_db):
        _seed_user()
        for key in ('meal', 'clothes', 'wash', 'teeth', 'toilet'):
            state = routine_service.complete_step('daughter', 'am', key, now=_at(6, 0))
        assert state['current_step_index'] == 5  # 'free'(チェックポイント)が現在地
        assert state['in_free_time'] is True
        assert state['steps'][5]['status'] == 'current'

    def test_unchecking_after_all_done_reverts_to_checklist_phase(self, isolated_db):
        """5項目全てチェックして自由時間に入った後、1つ取り消すとチェックリスト
        フェーズに戻り、まだ出発(チェックポイント通過)していない扱いになる。"""
        _seed_user()
        for key in ('meal', 'clothes', 'wash', 'teeth', 'toilet'):
            routine_service.complete_step('daughter', 'am', key, now=_at(6, 0))
        state = routine_service.complete_step('daughter', 'am', 'toilet', now=_at(6, 1))
        assert state['current_step_index'] == 0
        assert state['in_free_time'] is False
        assert state['steps'][5]['status'] == 'locked'  # 'free'

    def test_unknown_step_key_returns_404(self, isolated_db):
        from fastapi import HTTPException
        _seed_user()
        with pytest.raises(HTTPException) as exc_info:
            routine_service.complete_step('daughter', 'am', 'not_a_real_step', now=_at(6, 0))
        assert exc_info.value.status_code == 404

    def test_wrong_step_key_returns_409_for_non_checklist_step(self, isolated_db):
        """チェックリストでないステップ(自由時間より後)は引き続き逐次進行のまま。"""
        from fastapi import HTTPException
        _seed_user()
        for key in ('meal', 'clothes', 'wash', 'teeth', 'toilet'):
            routine_service.complete_step('daughter', 'am', key, now=_at(6, 0))
        # 'free'は締切前なので直接完了しようとすると400、'leave'は現在地ではないので409
        with pytest.raises(HTTPException) as exc_info:
            routine_service.complete_step('daughter', 'am', 'leave', now=_at(6, 1))
        assert exc_info.value.status_code == 409

    def test_completing_checkpoint_step_directly_is_rejected(self, isolated_db):
        from fastapi import HTTPException
        _seed_user()
        for key in ('meal', 'clothes', 'wash', 'teeth', 'toilet'):
            routine_service.complete_step('daughter', 'am', key, now=_at(6, 0))
        with pytest.raises(HTTPException) as exc_info:
            routine_service.complete_step('daughter', 'am', 'free', now=_at(6, 30))
        assert exc_info.value.status_code == 400

    def test_toggling_checklist_step_after_departure_is_rejected(self, isolated_db):
        """チェックポイントを過ぎて出発済みになった後は、チェックリストの
        取り消し・再チェックはできない(要件確認済み: ボーナス確定後はロック)。"""
        from fastapi import HTTPException
        _seed_user(gold=0, exp=0)
        for key in ('meal', 'clothes', 'wash', 'teeth', 'toilet'):
            routine_service.complete_step('daughter', 'am', key, now=_at(6, 0))
        routine_service.get_today_state('daughter', now=_at(7, 51))  # チェックポイント強制通過
        with pytest.raises(HTTPException) as exc_info:
            routine_service.complete_step('daughter', 'am', 'toilet', now=_at(7, 52))
        assert exc_info.value.status_code == 400

    def test_completing_before_flow_start_is_rejected(self, isolated_db):
        from fastapi import HTTPException
        _seed_user()
        with pytest.raises(HTTPException) as exc_info:
            routine_service.complete_step('daughter', 'am', 'wash', now=_at(4, 0))
        assert exc_info.value.status_code == 400


class TestMorningChecklistTvUnlock:
    """（毎朝ミッション統合で新規追加）朝の準備チェックリストが新たに全項目達成
    状態へ遷移した瞬間、旧「毎朝ミッション」クエスト承認時と同じTV電源ON処理
    (switchbot_service.trigger_tv_unlock)を呼ぶことのテスト。"""

    def test_completing_all_am_checklist_items_triggers_tv_unlock(self, isolated_db, monkeypatch):
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "plug-1")
        mock_trigger = MagicMock()
        monkeypatch.setattr(switchbot_service, "trigger_tv_unlock", mock_trigger)
        _seed_user(role='role_child')

        for key in ('meal', 'clothes', 'wash', 'teeth'):
            routine_service.complete_step('daughter', 'am', key, now=_at(6, 0))
        mock_trigger.assert_not_called()  # まだ4/5項目なので発火しない

        routine_service.complete_step('daughter', 'am', 'toilet', now=_at(6, 0))
        mock_trigger.assert_called_once()

    def test_tv_unlock_not_triggered_without_device_id_configured(self, isolated_db, monkeypatch):
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", None)
        mock_trigger = MagicMock()
        monkeypatch.setattr(switchbot_service, "trigger_tv_unlock", mock_trigger)
        _seed_user(role='role_child')

        for key in ('meal', 'clothes', 'wash', 'teeth', 'toilet'):
            routine_service.complete_step('daughter', 'am', key, now=_at(6, 0))
        mock_trigger.assert_not_called()

    def test_tv_unlock_not_triggered_for_adult_role(self, isolated_db, monkeypatch):
        """親自身がルーティンを完了させても、子供向けのTV解錠報酬は発火しない。"""
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "plug-1")
        mock_trigger = MagicMock()
        monkeypatch.setattr(switchbot_service, "trigger_tv_unlock", mock_trigger)
        _seed_user(user_id='dad', role='role_adult')

        for key in ('meal', 'clothes', 'wash', 'teeth', 'toilet'):
            routine_service.complete_step('dad', 'am', key, now=_at(6, 0))
        mock_trigger.assert_not_called()

    def test_tv_unlock_not_triggered_by_pm_night_checklist(self, isolated_db, monkeypatch):
        """pmの寝る準備チェックリストは対象外(amのみ)。"""
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "plug-1")
        mock_trigger = MagicMock()
        monkeypatch.setattr(switchbot_service, "trigger_tv_unlock", mock_trigger)
        _seed_user(role='role_child')

        for key in ('handwash', 'snack', 'homework', 'tomorrow_prep'):
            routine_service.complete_step('daughter', 'pm', key, now=_at(14, 0))
        routine_service.get_today_state('daughter', now=_at(18, 1))  # チェックポイント通過
        for key in ('dinner', 'bath', 'nightclothes', 'nightteeth'):
            routine_service.complete_step('daughter', 'pm', key, now=_at(18, 5))

        mock_trigger.assert_not_called()

    def test_tv_unlock_fires_again_after_uncheck_and_recomplete(self, isolated_db, monkeypatch):
        """全達成→1つ取り消し→再チェック、で再度「新たに全達成」になった場合は
        再度発火する(取り消し自体では発火しない)。"""
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "plug-1")
        mock_trigger = MagicMock()
        monkeypatch.setattr(switchbot_service, "trigger_tv_unlock", mock_trigger)
        _seed_user(role='role_child')

        for key in ('meal', 'clothes', 'wash', 'teeth', 'toilet'):
            routine_service.complete_step('daughter', 'am', key, now=_at(6, 0))
        assert mock_trigger.call_count == 1

        routine_service.complete_step('daughter', 'am', 'toilet', now=_at(6, 1))  # 取り消し
        assert mock_trigger.call_count == 1  # 取り消しでは発火しない

        routine_service.complete_step('daughter', 'am', 'toilet', now=_at(6, 2))  # 再チェック
        assert mock_trigger.call_count == 2


class TestCheckpointBonus:
    def test_full_completion_awards_full_bonus(self, isolated_db):
        _seed_user(gold=0, exp=0)
        for key in ('meal', 'clothes', 'wash', 'teeth', 'toilet'):
            routine_service.complete_step('daughter', 'am', key, now=_at(6, 0))

        # 7:50を過ぎてから状態取得すると、チェックポイントを強制的に通過する
        state = routine_service.get_today_state('daughter', now=_at(7, 51))
        am = state['flows']['am']
        assert am['bonus_gold'] == 150
        assert am['bonus_exp'] == 30
        assert am['current_step_index'] == 6  # 'leave' が現在地
        assert am['steps'][5]['status'] == 'done'  # 'free' も通過済みとして完了扱い

        with common.get_db_cursor() as cur:
            row = cur.execute("SELECT gold, exp FROM quest_users WHERE user_id='daughter'").fetchone()
        assert row['gold'] == 150
        assert row['exp'] == 30
        # コードレビューで発覚: 以前はleveled_up/new_levelがレスポンスに含まれず、
        # レベルアップしてもフロントがLEVEL UPトーストを出す手段が無かった。
        # new_levelは(quest_service._apply_quest_rewardsと同様)レベルアップの有無に
        # 関わらず常に付与後の実際のレベルを返す。フロントはleveled_upの方でゲートする。
        assert am['leveled_up'] is False
        assert am['new_level'] == 1

    def test_checkpoint_bonus_level_up_is_reported_in_response(self, isolated_db):
        """チェックポイント通過ボーナスでレベルアップした場合、その1回のレスポンスに
        leveled_up/new_levelが載ること(#コードレビューで発覚した欠落の回帰防止)。
        Lv1→2の必要経験値は100(game_logic.calculate_next_level_exp)なので、
        既存exp=80 + 満額ボーナスexp=30 = 110 でレベルアップする。
        """
        _seed_user(gold=0, exp=80)
        for key in ('meal', 'clothes', 'wash', 'teeth', 'toilet'):
            routine_service.complete_step('daughter', 'am', key, now=_at(6, 0))

        state = routine_service.get_today_state('daughter', now=_at(7, 51))
        am = state['flows']['am']
        assert am['leveled_up'] is True
        assert am['new_level'] == 2

        with common.get_db_cursor() as cur:
            row = cur.execute("SELECT level, exp FROM quest_users WHERE user_id='daughter'").fetchone()
        assert row['level'] == 2
        assert row['exp'] == 10

    def test_partial_completion_prorates_bonus_and_marks_remind(self, isolated_db):
        _seed_user(gold=0, exp=0)
        # 5項目中2つだけ完了させる
        routine_service.complete_step('daughter', 'am', 'wash', now=_at(6, 0))
        routine_service.complete_step('daughter', 'am', 'meal', now=_at(6, 5))

        state = routine_service.get_today_state('daughter', now=_at(7, 51))
        am = state['flows']['am']
        assert am['bonus_gold'] == 60  # round(150 * 2/5)
        assert am['bonus_exp'] == 12  # round(30 * 2/5)

        statuses = {s['key']: s['status'] for s in am['steps']}
        assert statuses['wash'] == 'done'
        assert statuses['meal'] == 'done'
        assert statuses['clothes'] == 'remind'
        assert statuses['teeth'] == 'remind'
        assert statuses['toilet'] == 'remind'

        with common.get_db_cursor() as cur:
            row = cur.execute("SELECT gold, exp FROM quest_users WHERE user_id='daughter'").fetchone()
        assert row['gold'] == 60
        assert row['exp'] == 12

    def test_preview_bonus_gold_grows_as_checklist_items_are_checked(self, isolated_db):
        """出発ボーナスの見込み額は、チェックポイント通過前でもチェックした分だけ
        画面に反映される(要件確認済み: チェックのたびに増えていくのが分かるように)。"""
        _seed_user()
        state = routine_service.get_today_state('daughter', now=_at(6, 0))
        assert state['flows']['am']['preview_bonus_gold'] == 0
        assert state['flows']['am']['bonus_full_gold'] == 150

        state = routine_service.complete_step('daughter', 'am', 'meal', now=_at(6, 1))
        assert state['preview_bonus_gold'] == 30  # round(150 * 1/5)

        state = routine_service.complete_step('daughter', 'am', 'wash', now=_at(6, 2))
        assert state['preview_bonus_gold'] == 60  # round(150 * 2/5)

        # チェックポイント通過後は最終的なbonus_goldと一致する(値が飛ばない)。
        state = routine_service.get_today_state('daughter', now=_at(7, 51))
        am = state['flows']['am']
        assert am['preview_bonus_gold'] == am['bonus_gold'] == 60

    def test_checkpoint_passage_is_idempotent(self, isolated_db):
        """チェックポイント通過後に何度状態取得しても、ボーナスは1回しか付与されない。"""
        _seed_user(gold=0, exp=0)
        for key in ('meal', 'clothes', 'wash', 'teeth', 'toilet'):
            routine_service.complete_step('daughter', 'am', key, now=_at(6, 0))

        routine_service.get_today_state('daughter', now=_at(7, 51))
        routine_service.get_today_state('daughter', now=_at(8, 30))
        routine_service.get_today_state('daughter', now=_at(9, 0))

        with common.get_db_cursor() as cur:
            row = cur.execute("SELECT gold, exp FROM quest_users WHERE user_id='daughter'").fetchone()
        assert row['gold'] == 150
        assert row['exp'] == 30

    def test_forced_transition_while_still_in_checklist_phase(self, isolated_db):
        """チェックリストが途中(自由時間に入る前)で締切時刻を過ぎても強制的に通過する。"""
        _seed_user(gold=0, exp=0)
        routine_service.complete_step('daughter', 'am', 'wash', now=_at(6, 0))
        # チェックリストが1/5しか終わっていないまま7:50を過ぎる
        state = routine_service.get_today_state('daughter', now=_at(8, 0))
        am = state['flows']['am']
        assert am['current_step_index'] == 6
        assert am['bonus_gold'] == round(150 * (1 / 5))


class TestWeekendCheckpointOverride:
    """土日は朝(am)のチェックポイントだけ09:30に後ろ倒しする(要件: なるべく平日と揃える)。"""

    def test_am_checkpoint_time_field_is_930_on_saturday(self, isolated_db):
        _seed_user()
        state = routine_service.get_today_state('daughter', now=_saturday_at(6, 0))
        assert state['flows']['am']['checkpoint_time'] == '09:30'

    def test_am_checkpoint_time_field_is_750_on_weekday(self, isolated_db):
        _seed_user()
        state = routine_service.get_today_state('daughter', now=_at(6, 0))
        assert state['flows']['am']['checkpoint_time'] == '07:50'

    def test_am_not_forced_past_at_800_on_saturday(self, isolated_db):
        """平日なら7:50超えで強制通過する8:00でも、土日は09:30まではまだ猶予がある。"""
        _seed_user(gold=0, exp=0)
        for key in ('meal', 'clothes', 'wash', 'teeth', 'toilet'):
            routine_service.complete_step('daughter', 'am', key, now=_saturday_at(6, 0))
        state = routine_service.get_today_state('daughter', now=_saturday_at(8, 0))
        am = state['flows']['am']
        assert am['in_free_time'] is True
        assert am['bonus_gold'] == 0

    def test_am_forced_past_at_931_on_saturday(self, isolated_db):
        _seed_user(gold=0, exp=0)
        for key in ('meal', 'clothes', 'wash', 'teeth', 'toilet'):
            routine_service.complete_step('daughter', 'am', key, now=_saturday_at(6, 0))
        state = routine_service.get_today_state('daughter', now=_saturday_at(9, 31))
        am = state['flows']['am']
        assert am['in_free_time'] is False
        assert am['bonus_gold'] == 150

    def test_pm_checkpoint_time_unchanged_on_saturday(self, isolated_db):
        """夕方(pm)は土日も平日と同じ18:00のまま(要件確認済み: 現状維持)。"""
        _seed_user()
        state = routine_service.get_today_state('daughter', now=_saturday_at(15, 0))
        assert state['flows']['pm']['checkpoint_time'] == '18:00'


class TestWeekendPmSkipAndCarryover:
    """休日PMの微修正: handwashは常にスキップ、宿題は金曜/土曜の完了を引き継ぐ。"""

    def test_saturday_pm_starts_from_snack(self, isolated_db):
        _seed_user()
        state = routine_service.get_today_state('daughter', now=_saturday_at(14, 0))
        pm = state['flows']['pm']
        statuses = {s['key']: s['status'] for s in pm['steps']}
        assert statuses['handwash'] == 'done'
        assert statuses['snack'] == 'current'
        assert pm['current_step_index'] == 1

    def test_sunday_pm_starts_from_snack(self, isolated_db):
        _seed_user()
        state = routine_service.get_today_state('daughter', now=_sunday_at(14, 0))
        pm = state['flows']['pm']
        statuses = {s['key']: s['status'] for s in pm['steps']}
        assert statuses['handwash'] == 'done'
        assert statuses['snack'] == 'current'
        assert pm['current_step_index'] == 1

    def test_homework_done_friday_skips_saturday_and_sunday(self, isolated_db):
        _seed_user()
        for key in ('handwash', 'snack', 'homework'):
            routine_service.complete_step('daughter', 'pm', key, now=_friday_at(14, 0))

        sat_state = routine_service.get_today_state('daughter', now=_saturday_at(14, 0))
        pm_sat = sat_state['flows']['pm']
        statuses_sat = {s['key']: s['status'] for s in pm_sat['steps']}
        assert statuses_sat['handwash'] == 'done'
        assert statuses_sat['homework'] == 'done'
        assert statuses_sat['snack'] == 'current'
        assert pm_sat['current_step_index'] == 1

        sun_state = routine_service.get_today_state('daughter', now=_sunday_at(14, 0))
        pm_sun = sun_state['flows']['pm']
        statuses_sun = {s['key']: s['status'] for s in pm_sun['steps']}
        assert statuses_sun['homework'] == 'done'
        assert statuses_sun['snack'] == 'current'

    def test_homework_done_friday_skips_sunday_even_without_visiting_saturday(self, isolated_db):
        """土曜のアプリ未起動(routine_progress行が無い)でも、金曜分の記録から
        日曜のスキップ判定ができること(2日分の遡り: 金曜・土曜)。"""
        _seed_user()
        for key in ('handwash', 'snack', 'homework'):
            routine_service.complete_step('daughter', 'pm', key, now=_friday_at(14, 0))

        sun_state = routine_service.get_today_state('daughter', now=_sunday_at(14, 0))
        pm_sun = sun_state['flows']['pm']
        statuses_sun = {s['key']: s['status'] for s in pm_sun['steps']}
        assert statuses_sun['homework'] == 'done'

    def test_homework_done_saturday_only_skips_sunday_not_saturday(self, isolated_db):
        _seed_user()
        # 金曜は手洗い・おやつだけ完了し、宿題は完了しないまま
        routine_service.complete_step('daughter', 'pm', 'handwash', now=_friday_at(14, 0))
        routine_service.complete_step('daughter', 'pm', 'snack', now=_friday_at(14, 5))

        sat_state = routine_service.get_today_state('daughter', now=_saturday_at(14, 0))
        pm_sat = sat_state['flows']['pm']
        statuses_sat = {s['key']: s['status'] for s in pm_sat['steps']}
        assert statuses_sat['handwash'] == 'done'  # 土日は常にスキップ
        assert statuses_sat['homework'] == 'locked'  # 金曜未完了なのでスキップされない
        assert pm_sat['current_step_index'] == 1  # snackが現在地

        routine_service.complete_step('daughter', 'pm', 'snack', now=_saturday_at(14, 5))
        routine_service.complete_step('daughter', 'pm', 'homework', now=_saturday_at(14, 10))

        sun_state = routine_service.get_today_state('daughter', now=_sunday_at(14, 0))
        pm_sun = sun_state['flows']['pm']
        statuses_sun = {s['key']: s['status'] for s in pm_sun['steps']}
        assert statuses_sun['homework'] == 'done'  # 土曜に完了したので日曜はスキップ
        assert statuses_sun['snack'] == 'current'  # snack自体は日曜も必要

    def test_homework_marked_remind_on_friday_does_not_skip_saturday(self, isolated_db):
        """金曜にチェックポイントを過ぎて未完了扱い('remind')になった場合は
        'done'ではないため、土曜はスキップされない。"""
        _seed_user(gold=0, exp=0)
        routine_service.complete_step('daughter', 'pm', 'handwash', now=_friday_at(14, 0))
        routine_service.complete_step('daughter', 'pm', 'snack', now=_friday_at(14, 5))
        routine_service.get_today_state('daughter', now=_friday_at(18, 1))  # 強制通過

        sat_state = routine_service.get_today_state('daughter', now=_saturday_at(14, 0))
        pm_sat = sat_state['flows']['pm']
        statuses_sat = {s['key']: s['status'] for s in pm_sat['steps']}
        assert statuses_sat['homework'] == 'locked'

    def test_weekday_friday_homework_never_skipped(self, isolated_db):
        """金曜自身は平日なのでcarryover判定の対象外(常に通常どおり表示)。"""
        _seed_user()
        state = routine_service.get_today_state('daughter', now=_friday_at(14, 0))
        pm = state['flows']['pm']
        statuses = {s['key']: s['status'] for s in pm['steps']}
        assert statuses['handwash'] == 'current'
        assert statuses['homework'] == 'locked'

    def test_skipped_steps_count_toward_full_bonus_ratio(self, isolated_db):
        """スキップ扱い('done')は達成率計算でも達成済みとしてカウントされる。"""
        _seed_user(gold=0, exp=0)
        for key in ('handwash', 'snack', 'homework', 'tomorrow_prep'):
            routine_service.complete_step('daughter', 'pm', key, now=_friday_at(14, 0))

        # 土曜はhandwash・homework・tomorrow_prepともスキップ済みなのでsnackだけ
        # 完了させれば満額ボーナス。
        routine_service.complete_step('daughter', 'pm', 'snack', now=_saturday_at(14, 0))
        state = routine_service.get_today_state('daughter', now=_saturday_at(18, 1))
        pm_sat = state['flows']['pm']
        assert pm_sat['bonus_gold'] == 150
        assert pm_sat['bonus_exp'] == 30


class TestPmEveningSplit:
    """夜の切り替え(自由時間→寝る準備)を18:00にし、寝る準備を晩ごはん・お風呂・
    着替え・歯磨きの4項目(順不同チェックリスト)に分割し、宿題の次に「明日の準備」を
    追加した機能(要件確認済み)のテスト。"""

    def _finish_pre_checkpoint_steps(self, now):
        for key in ('handwash', 'snack', 'homework', 'tomorrow_prep'):
            routine_service.complete_step('daughter', 'pm', key, now=now)

    def test_step_order_includes_tomorrow_prep_and_night_checklist(self, isolated_db):
        _seed_user()
        state = routine_service.get_today_state('daughter', now=_at(14, 0))
        pm = state['flows']['pm']
        assert [s['key'] for s in pm['steps']] == [
            'handwash', 'snack', 'homework', 'tomorrow_prep', 'free',
            'dinner', 'bath', 'nightclothes', 'nightteeth', 'sleep',
        ]
        checklist_flags = {s['key']: s['is_checklist'] for s in pm['steps']}
        assert checklist_flags['dinner'] is True
        assert checklist_flags['bath'] is True
        assert checklist_flags['nightclothes'] is True
        assert checklist_flags['nightteeth'] is True
        assert checklist_flags['tomorrow_prep'] is False
        assert checklist_flags['free'] is False

    def test_night_checklist_locked_before_checkpoint(self, isolated_db):
        """寝る準備4項目は、自由時間→寝る準備のチェックポイント(18:00)を通過する
        までは'locked'で、トグルしようとすると400になる(要件外の事故防止)。"""
        from fastapi import HTTPException
        _seed_user()
        self._finish_pre_checkpoint_steps(now=_at(14, 0))
        state = routine_service.get_today_state('daughter', now=_at(17, 0))
        statuses = {s['key']: s['status'] for s in state['flows']['pm']['steps']}
        assert statuses['dinner'] == 'locked'

        with pytest.raises(HTTPException) as exc_info:
            routine_service.complete_step('daughter', 'pm', 'dinner', now=_at(17, 1))
        assert exc_info.value.status_code == 400

    def test_night_checklist_activates_all_at_once_after_checkpoint(self, isolated_db):
        _seed_user(gold=0, exp=0)
        self._finish_pre_checkpoint_steps(now=_at(14, 0))
        state = routine_service.get_today_state('daughter', now=_at(18, 1))
        pm = state['flows']['pm']
        statuses = {s['key']: s['status'] for s in pm['steps']}
        assert all(statuses[k] == 'current' for k in ('dinner', 'bath', 'nightclothes', 'nightteeth'))
        assert statuses['sleep'] == 'locked'
        assert pm['current_step_index'] == 5  # 'dinner'
        assert pm['in_free_time'] is False
        assert pm['bonus_gold'] == 150  # 出発ボーナスは既に確定済み(寝る準備とは無関係)

    def test_completing_night_checklist_in_any_order(self, isolated_db):
        _seed_user()
        self._finish_pre_checkpoint_steps(now=_at(14, 0))
        routine_service.get_today_state('daughter', now=_at(18, 1))  # チェックポイント通過

        state = routine_service.complete_step('daughter', 'pm', 'nightteeth', now=_at(18, 5))
        statuses = {s['key']: s['status'] for s in state['steps']}
        assert statuses['nightteeth'] == 'done'
        assert statuses['dinner'] == 'current'  # 他の項目は引き続きチェック可能
        assert state['current_step_index'] == 5  # フェーズの目印は不変

    def test_all_night_checklist_done_advances_to_sleep(self, isolated_db):
        _seed_user()
        self._finish_pre_checkpoint_steps(now=_at(14, 0))
        routine_service.get_today_state('daughter', now=_at(18, 1))
        state = None
        for key in ('dinner', 'bath', 'nightclothes', 'nightteeth'):
            state = routine_service.complete_step('daughter', 'pm', key, now=_at(18, 5))
        assert state['current_step_index'] == 9  # 'sleep'
        assert state['in_free_time'] is False  # 'sleep'にはcheckpoint_timeが無いため
        assert state['steps'][9]['status'] == 'current'

    def test_unchecking_after_all_night_items_done_reverts_to_checklist_phase(self, isolated_db):
        _seed_user()
        self._finish_pre_checkpoint_steps(now=_at(14, 0))
        routine_service.get_today_state('daughter', now=_at(18, 1))
        for key in ('dinner', 'bath', 'nightclothes', 'nightteeth'):
            routine_service.complete_step('daughter', 'pm', key, now=_at(18, 5))

        state = routine_service.complete_step('daughter', 'pm', 'bath', now=_at(18, 10))
        assert state['current_step_index'] == 5  # 'dinner'に巻き戻る
        assert state['steps'][9]['status'] == 'locked'  # 'sleep'

    def test_wrong_step_key_returns_409_before_night_checklist_done(self, isolated_db):
        """寝る準備4項目が終わるまでは'sleep'を直接完了できない。"""
        from fastapi import HTTPException
        _seed_user()
        self._finish_pre_checkpoint_steps(now=_at(14, 0))
        routine_service.get_today_state('daughter', now=_at(18, 1))
        with pytest.raises(HTTPException) as exc_info:
            routine_service.complete_step('daughter', 'pm', 'sleep', now=_at(18, 5))
        assert exc_info.value.status_code == 409

    def test_toggling_night_checklist_after_sleep_completed_is_rejected(self, isolated_db):
        """就寝まで完了した後は、寝る準備のチェック変更もできない(その日のフローは
        完了しているため)。"""
        from fastapi import HTTPException
        _seed_user()
        self._finish_pre_checkpoint_steps(now=_at(14, 0))
        routine_service.get_today_state('daughter', now=_at(18, 1))
        for key in ('dinner', 'bath', 'nightclothes', 'nightteeth'):
            routine_service.complete_step('daughter', 'pm', key, now=_at(18, 5))
        routine_service.complete_step('daughter', 'pm', 'sleep', now=_at(18, 10))

        with pytest.raises(HTTPException) as exc_info:
            routine_service.complete_step('daughter', 'pm', 'dinner', now=_at(18, 15))
        assert exc_info.value.status_code == 400

    def test_tomorrow_prep_done_friday_skips_saturday_and_sunday(self, isolated_db):
        """明日の準備は宿題と同じ繰越ルール(要件確認済み)。"""
        _seed_user()
        for key in ('handwash', 'snack', 'homework', 'tomorrow_prep'):
            routine_service.complete_step('daughter', 'pm', key, now=_friday_at(14, 0))

        sat_state = routine_service.get_today_state('daughter', now=_saturday_at(14, 0))
        statuses_sat = {s['key']: s['status'] for s in sat_state['flows']['pm']['steps']}
        assert statuses_sat['tomorrow_prep'] == 'done'

        sun_state = routine_service.get_today_state('daughter', now=_sunday_at(14, 0))
        statuses_sun = {s['key']: s['status'] for s in sun_state['flows']['pm']['steps']}
        assert statuses_sun['tomorrow_prep'] == 'done'

    def test_partial_pre_checkpoint_completion_prorates_bonus_over_four_steps(self, isolated_db):
        """出発ボーナスの対象は自由時間より前の4項目(handwash/snack/homework/
        tomorrow_prep)になった(明日の準備の追加により3→4項目に変更)。"""
        _seed_user(gold=0, exp=0)
        routine_service.complete_step('daughter', 'pm', 'handwash', now=_at(14, 0))
        routine_service.complete_step('daughter', 'pm', 'snack', now=_at(14, 5))

        state = routine_service.get_today_state('daughter', now=_at(18, 1))
        pm = state['flows']['pm']
        assert pm['bonus_gold'] == round(150 * (2 / 4))
        assert pm['bonus_exp'] == round(30 * (2 / 4))


class TestRouterHttp:
    def test_get_today_unknown_user_returns_404(self, api_client):
        res = api_client.get("/api/routine/today", params={"user_id": "nobody"})
        assert res.status_code == 404

    def test_complete_invalid_flow_key_returns_422(self, api_client):
        res = api_client.post(
            "/api/routine/complete",
            json={"user_id": "daughter", "flow_key": "evening", "step_key": "wash"},
        )
        assert res.status_code == 422

    def test_get_today_missing_user_id_returns_422(self, api_client):
        res = api_client.get("/api/routine/today")
        assert res.status_code == 422
