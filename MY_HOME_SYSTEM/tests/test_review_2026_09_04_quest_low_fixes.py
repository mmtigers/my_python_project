# MY_HOME_SYSTEM/tests/test_review_2026_09_04_quest_low_fixes.py
"""
Issue #409(Family Quest の Low・保守性指摘)の回帰テスト。

- Q-L1 連続達成ボーナスが承認待ちの日を「サボり」と誤判定しない
- Q-L3 承認済み履歴のキャンセルでメダルも戻る(medals_earned、migration 0009)
- Q-L4 リクエストモデルの int 上限(2**63 超で 500 にならない)
- Q-L5 total_quests に承認待ち行・アイテム使用行を含めない
- Q-L6 filename 無しのアップロードは 400
- Q-L9 save_logs_batch_generic の識別子ホワイトリスト
- Q-L10 存在しない user_id ではロック辞書にエントリを作らない
- 品質: MasterQuest/MasterReward の値域検証
"""
import datetime
import os
import sys
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
from core import database as core_db
from models.quest import MasterQuest, MasterReward, QuestAction
from services import quest_service as qs_module
from services.quest_service import ROLE_ADULT, ROLE_CHILD


def _seed(role=ROLE_ADULT, gold=0):
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, medal_count, role) "
            "VALUES ('dad', 'Dad', 'Warrior', 1, 0, ?, 0, ?)", (gold, role),
        )
        cur.execute(
            "INSERT INTO quest_master (quest_id, title, exp_gain, gold_gain, quest_type, target_user) "
            "VALUES (901, 'テスト', 10, 100, 'daily', 'all')"
        )


def _quiet(monkeypatch):
    monkeypatch.setattr(qs_module.notification_service, "send_push", lambda *a, **k: True)
    monkeypatch.setattr(qs_module.sound_manager, "play", lambda *a, **k: None)


def test_bonus_treats_pending_day_as_done(isolated_db):
    _seed()
    # ボーナス判定は JST 日付基準なので、SQLite の datetime('now')(UTC)ではなく
    # JST で日付を作る(UTC 15時以降は UTC 日付と JST 日付がずれて壁時計依存になる)
    now_jst = datetime.datetime.now(qs_module.JST)
    three_days_ago = (now_jst - datetime.timedelta(days=3)).isoformat()
    two_days_ago = (now_jst - datetime.timedelta(days=2)).isoformat()
    with common.get_db_cursor(commit=True) as cur:
        # 3日前: approved、2日前: pending(承認待ち)。昨日は無し → 欠席は1日だけ
        cur.execute("INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status) "
                    "VALUES ('dad', 901, 'テスト', 10, 100, ?, 'approved')", (three_days_ago,))
        cur.execute("INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status) "
                    "VALUES ('dad', 901, 'テスト', 0, 0, ?, 'pending')", (two_days_ago,))
        quest = cur.execute("SELECT * FROM quest_master WHERE quest_id = 901").fetchone()
        boost = qs_module.QuestService().calculate_quest_boost(cur, "dad", quest)
    # 最終実施(pending)は2日前 → days_diff=2 → missed=1 → 10%
    assert boost["gold"] == 10


def test_cancel_reverts_medals(isolated_db, monkeypatch):
    _quiet(monkeypatch)
    _seed(gold=0)
    monkeypatch.setattr(qs_module.game_logic.GameLogic, "calculate_drop_rewards",
                        staticmethod(lambda g, e: {"gold": g, "exp": e, "medals": 2, "is_lucky": True}))
    service = qs_module.QuestService()
    service.process_complete_quest("dad", 901)
    with common.get_db_cursor() as cur:
        assert cur.execute("SELECT medal_count FROM quest_users WHERE user_id='dad'").fetchone()[0] == 2
        hist = cur.execute("SELECT id, medals_earned FROM quest_history ORDER BY id DESC LIMIT 1").fetchone()
        assert hist["medals_earned"] == 2
    service.process_cancel_quest("dad", hist["id"])
    with common.get_db_cursor() as cur:
        assert cur.execute("SELECT medal_count FROM quest_users WHERE user_id='dad'").fetchone()[0] == 0


def test_request_model_rejects_out_of_range_ids():
    with pytest.raises(ValidationError):
        QuestAction(user_id="dad", quest_id=2**64)
    with pytest.raises(ValidationError):
        QuestAction(user_id="dad", quest_id=0)


def test_total_quests_excludes_pending_and_item_usage(isolated_db):
    _seed()
    with common.get_db_cursor(commit=True) as cur:
        for qid, status in ((901, 'approved'), (901, 'pending'), (0, 'approved'), (901, 'rejected')):
            cur.execute("INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status) "
                        "VALUES ('dad', ?, 't', 0, 0, datetime('now'), ?)", (qid, status))
    stats = qs_module.UserService().get_family_chronicle()["stats"]
    assert stats["totalQuests"] == 1


def test_upload_without_filename_returns_400(api_client):
    res = api_client.post("/api/quest/upload", files={"file": ("", b"\x89PNG\r\n\x1a\n", "image/png")})
    assert res.status_code in (400, 422)


def test_batch_insert_rejects_bad_identifiers(isolated_db):
    assert core_db.save_logs_batch_generic("daily_logs; DROP TABLE x", ["a"], [("v",)]) is False
    assert core_db.save_logs_batch_generic("daily_logs", ["a b"], [("v",)]) is False


def test_complete_for_unknown_user_does_not_create_lock(isolated_db, monkeypatch):
    _quiet(monkeypatch)
    _seed()
    before = set(qs_module._user_balance_locks.keys())
    with pytest.raises(HTTPException) as exc:
        qs_module.QuestService().process_complete_quest("ghost_user", 901)
    assert exc.value.status_code == 404
    assert "ghost_user" not in set(qs_module._user_balance_locks.keys()) - before


def test_master_models_validate_domains():
    base = dict(id=1, title="t", type="daily", exp=1, gold=1, icon="x")
    assert MasterQuest(**base).days is None
    with pytest.raises(ValidationError):
        MasterQuest(**{**base, "type": "dayly"})
    with pytest.raises(ValidationError):
        MasterQuest(**{**base, "days": "0,,1"})
    with pytest.raises(ValidationError):
        MasterQuest(**{**base, "gold": -5})
    with pytest.raises(ValidationError):
        MasterReward(id=1, title="r", category="c", cost_gold=-1, icon_key="k")
