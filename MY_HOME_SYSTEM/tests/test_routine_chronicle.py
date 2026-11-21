# MY_HOME_SYSTEM/tests/test_routine_chronicle.py
"""
すごろく(デイリールーティン)の達成が冒険の記録(GET /api/quest/family/chronicle)に
載ることのテスト。

記録は区間(朝のじゅんび / 帰ってからのじゅんび / 寝るまえのじゅんび)ごとに1行へ
まとめ、その日の最終状態で 'done' のステップだけを数える。
"""
import datetime

from freezegun import freeze_time

from core.database import get_db_cursor
from services.quest.user_service import UserService
from services.routine_service import routine_service

JST = datetime.timezone(datetime.timedelta(hours=9), 'JST')
MONDAY = datetime.datetime(2024, 1, 15, tzinfo=JST)  # 祝日ではない平日


def _seed_user(user_id='daughter', name='すずか', role='role_child'):
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) "
            "VALUES (?, ?, ?, 1, 0, 0, ?)",
            (user_id, name, 'Novice', role),
        )


def _at(hour, minute, days=0):
    return (MONDAY + datetime.timedelta(days=days)).replace(hour=hour, minute=minute)


def _routine_entries(now):
    with freeze_time(now):
        chronicle = UserService().get_family_chronicle()["chronicle"]
    return [ev for ev in chronicle if ev["type"] == "routine"]


class TestRoutineInChronicle:
    def test_morning_checklist_is_summarized_into_one_entry(self, isolated_db):
        _seed_user()
        routine_service.complete_step('daughter', 'am', 'meal', now=_at(6, 30))
        routine_service.complete_step('daughter', 'am', 'teeth', now=_at(6, 45))

        entries = _routine_entries(_at(7, 0))

        assert len(entries) == 1
        ev = entries[0]
        assert ev["userId"] == 'daughter'
        assert ev["title"] == '朝のじゅんび'
        assert ev["text"] == 'すずかは 朝のじゅんび を 2/5 できた！'
        assert ev["detail"] == '朝ごはん・歯磨き'
        assert ev["dateStr"] == '2024-01-15'
        # 時刻は最後にチェックした時刻
        assert ev["timestamp"].startswith('2024-01-15T06:45')

    def test_unchecked_step_is_not_counted(self, isolated_db):
        _seed_user()
        routine_service.complete_step('daughter', 'am', 'meal', now=_at(6, 30))
        routine_service.complete_step('daughter', 'am', 'wash', now=_at(6, 35))
        routine_service.complete_step('daughter', 'am', 'wash', now=_at(6, 40))  # チェックを外す

        entries = _routine_entries(_at(7, 0))

        assert [ev["detail"] for ev in entries] == ['朝ごはん']

    def test_no_entry_when_nothing_is_done(self, isolated_db):
        _seed_user()
        # 締切処理で行が作られ、全ステップが「まだだよ」になっても記録には出さない
        routine_service.process_deadlines(now=_at(8, 0))

        assert _routine_entries(_at(8, 0)) == []

    def test_departure_bonus_is_shown_as_gold(self, isolated_db):
        _seed_user()
        for key in ('meal', 'clothes', 'wash', 'teeth', 'toilet'):
            routine_service.complete_step('daughter', 'am', key, now=_at(6, 30))
        routine_service.process_deadlines(now=_at(7, 51))

        entries = _routine_entries(_at(8, 0))

        assert len(entries) == 1
        assert entries[0]["text"] == 'すずかは 朝のじゅんび を 5/5 できた！'
        assert entries[0]["gold"] == 150

    def test_pm_flow_is_split_before_and_after_free_time(self, isolated_db):
        _seed_user()
        routine_service.complete_step('daughter', 'pm', 'handwash', now=_at(15, 0))
        routine_service.complete_step('daughter', 'pm', 'snack', now=_at(15, 10))
        routine_service.complete_step('daughter', 'pm', 'homework', now=_at(16, 0))
        routine_service.process_deadlines(now=_at(17, 31))
        routine_service.complete_step('daughter', 'pm', 'bath', now=_at(19, 0))

        entries = _routine_entries(_at(20, 0))
        by_title = {ev["title"]: ev for ev in entries}

        assert set(by_title) == {'帰ってからのじゅんび', '寝るまえのじゅんび'}
        assert by_title['帰ってからのじゅんび']["detail"] == '手洗い・うがい・おやつ休憩・宿題'
        assert by_title['寝るまえのじゅんび']["detail"] == 'お風呂'
        # 自由時間(チェックポイント)自身は項目として数えない
        assert '自由時間' not in by_title['帰ってからのじゅんび']["detail"]

    def test_sleep_step_reward_is_included_in_gold(self, isolated_db):
        _seed_user()
        for key in ('handwash', 'snack', 'homework'):
            routine_service.complete_step('daughter', 'pm', key, now=_at(15, 0))
        routine_service.process_deadlines(now=_at(17, 31))
        for key in ('dinner', 'bath', 'nightclothes', 'nightteeth', 'tomorrow_prep'):
            routine_service.complete_step('daughter', 'pm', key, now=_at(19, 0))
        routine_service.complete_step('daughter', 'pm', 'sleep', now=_at(21, 0))

        entries = _routine_entries(_at(21, 5))
        night = next(ev for ev in entries if ev["title"] == '寝るまえのじゅんび')

        assert night["gold"] == 70  # 就寝ステップの個別報酬
        assert night["timestamp"].startswith('2024-01-15T21:00')

    def test_old_days_beyond_lookback_are_excluded(self, isolated_db):
        _seed_user()
        routine_service.complete_step('daughter', 'am', 'meal', now=_at(6, 30))

        assert len(_routine_entries(_at(7, 0, days=13))) == 1
        assert _routine_entries(_at(7, 0, days=14)) == []

    def test_entries_are_sorted_with_quests_by_time(self, isolated_db):
        _seed_user()
        routine_service.complete_step('daughter', 'am', 'meal', now=_at(6, 30))
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_history (user_id, quest_id, quest_title, gold_earned, exp_earned, "
                "completed_at, status) VALUES (?, ?, ?, ?, ?, ?, 'approved')",
                ('daughter', 1, 'お手伝い', 10, 5, _at(7, 0).isoformat()),
            )

        with freeze_time(_at(8, 0)):
            chronicle = UserService().get_family_chronicle()["chronicle"]

        assert [ev["type"] for ev in chronicle] == ['quest', 'routine']


class TestRoutineChronicleUsesThatDaysSteps:
    """Issue #1113: routine_data.py の定義を変えても、変更前の日の記録が変わらない。"""

    def _patch_flow_set(self, monkeypatch, edit):
        import services.routine_service as rs
        original = rs.get_flow_set

        def patched(user_id, role):
            flows = dict(original(user_id, role))
            flows['am'] = {**flows['am'], 'steps': edit(list(flows['am']['steps']))}
            return flows

        monkeypatch.setattr(rs, 'get_flow_set', patched)

    def test_removed_step_is_still_counted_with_its_key(self, isolated_db, monkeypatch):
        _seed_user()
        routine_service.complete_step('daughter', 'am', 'meal', now=_at(6, 30))
        routine_service.complete_step('daughter', 'am', 'teeth', now=_at(6, 45))
        # 記録したあとで「歯磨き」を定義から消す
        self._patch_flow_set(monkeypatch, lambda steps: [s for s in steps if s['key'] != 'teeth'])

        ev = _routine_entries(_at(7, 0))[0]

        assert ev["text"] == 'すずかは 朝のじゅんび を 2/5 できた！'
        assert ev["detail"] == '朝ごはん・teeth'

    def test_added_step_is_not_in_denominator(self, isolated_db, monkeypatch):
        _seed_user()
        routine_service.complete_step('daughter', 'am', 'meal', now=_at(6, 30))
        new_step = {
            'key': 'added_later', 'label': 'あとから足した', 'checkpoint_time': None,
            'checklist': True,
        }
        self._patch_flow_set(monkeypatch, lambda steps: [new_step] + steps)

        ev = _routine_entries(_at(7, 0))[0]

        assert ev["text"] == 'すずかは 朝のじゅんび を 1/5 できた！'
        assert ev["detail"] == '朝ごはん'
