# MY_HOME_SYSTEM/tests/test_quest_type_limited_random.py
"""Issue #529 の回帰テスト。

- MasterQuest.type が 'limited' / 'random' を受け付け、sync_master_data が中断しないこと
- start_date / end_date は quest_type を問わず評価されること(以前は 'limited' のみ)
- random 型の出現抽選が引き続き機能すること
"""
import datetime
import os
import sys

import pytest
from pydantic import ValidationError

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
from models.quest import MasterQuest
from services import quest_service as quest_service_module
from services.quest_service import GameSystem, QuestService


def _quest(**overrides):
    base = {
        "quest_id": 9100, "quest_type": "special", "start_date": None, "end_date": None,
        "occurrence_chance": 1.0, "start_time": None, "end_time": None, "day_of_week": None,
        "target_user": "all", "icon_key": "⭐",
    }
    base.update(overrides)
    return base


class TestMasterQuestTypeLiteral:
    @pytest.mark.parametrize("quest_type", ["daily", "special", "infinite", "limited", "random"])
    def test_accepts_all_supported_types(self, quest_type):
        q = MasterQuest(id=1, title="t", type=quest_type, exp=1, gold=1, icon="x")
        assert q.type == quest_type

    def test_rejects_unknown_type(self):
        with pytest.raises(ValidationError):
            MasterQuest(id=1, title="t", type="weekly", exp=1, gold=1, icon="x")

    @pytest.mark.parametrize("chance", [-0.1, 1.5])
    def test_chance_must_be_between_0_and_1(self, chance):
        with pytest.raises(ValidationError):
            MasterQuest(id=1, title="t", type="random", exp=1, gold=1, icon="x", chance=chance)


class TestSyncMasterDataWithLimitedAndRandom:
    def test_sync_does_not_abort_on_limited_or_random_quest(self, isolated_db, monkeypatch):
        import importlib
        import types
        # sync_master_data は importlib.reload(quest_data) を呼ぶため、実モジュール型で差し替え、
        # reload は恒等関数にする(monkeypatch で元に戻る)
        fake = types.ModuleType("quest_data_fake_529")
        monkeypatch.setattr(importlib, "reload", lambda m: m)
        fake.__dict__.update(
            USERS=[{"user_id": "dad", "name": "Dad", "job_class": "W", "role": "role_adult"}],
            QUESTS=[
                {"id": 1, "title": "期間限定", "type": "limited", "target": "all", "exp": 1, "gold": 1,
                 "icon": "⏳", "start_date": "2020-01-01", "end_date": "2020-01-02"},
                {"id": 2, "title": "抽選", "type": "random", "target": "all", "exp": 1, "gold": 1,
                 "icon": "🎲", "chance": 0.0},
            ],
            REWARDS=[],
        )
        monkeypatch.setattr(quest_service_module, "quest_data", fake)
        result = GameSystem().sync_master_data()
        assert result["status"] == "synced"
        with common.get_db_cursor() as cur:
            rows = {r["quest_id"]: dict(r) for r in cur.execute("SELECT * FROM quest_master")}
        assert rows[1]["quest_type"] == "limited" and rows[1]["end_date"] == "2020-01-02"
        assert rows[2]["quest_type"] == "random" and rows[2]["occurrence_chance"] == 0.0


class TestDateRangeAppliesRegardlessOfType:
    def test_expired_special_quest_is_not_active(self):
        qs = QuestService()
        assert qs._is_quest_currently_active(_quest(quest_type="special", end_date="2020-01-02")) is False

    def test_future_daily_quest_is_not_active_yet(self):
        qs = QuestService()
        assert qs._is_quest_currently_active(_quest(quest_type="daily", start_date="2999-01-01")) is False

    def test_quest_within_range_is_active(self):
        qs = QuestService()
        today = datetime.datetime.now(quest_service_module.JST).date()
        q = _quest(quest_type="limited",
                   start_date=(today - datetime.timedelta(days=1)).isoformat(),
                   end_date=(today + datetime.timedelta(days=1)).isoformat())
        assert qs._is_quest_currently_active(q) is True

    def test_quest_without_dates_is_unaffected(self):
        qs = QuestService()
        assert qs._is_quest_currently_active(_quest(quest_type="special")) is True

    def test_random_quest_with_zero_chance_never_appears(self):
        qs = QuestService()
        assert qs._is_quest_currently_active(_quest(quest_type="random", occurrence_chance=0.0)) is False
