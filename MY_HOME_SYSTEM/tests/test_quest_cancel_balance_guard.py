# MY_HOME_SYSTEM/tests/test_quest_cancel_balance_guard.py
"""
#356: 承認済み履歴のキャンセル時にゴールドが0に飽和し、無限ゴールドループが成立していた
問題の回帰テスト。

以前の _revert_and_delete_history は `max(0, gold - gold_earned)` で残高を戻していたため、
「完了(+100G) → 100Gの報酬を購入(0G) → 履歴をキャンセル(0Gのまま) → 再完了(+100G)」の
サイクルで報酬が無料になっていた。修正後は、付与済みゴールドを既に消費している
(残高 < 付与額)場合はキャンセル自体を400で拒否する。
"""
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
from services import quest_service as qs_module
from services.quest_service import ROLE_ADULT


def _seed(gold: int = 0):
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) "
            "VALUES ('dad', 'Dad', 'Warrior', 1, 0, ?, ?)",
            (gold, ROLE_ADULT),
        )
        cur.execute(
            "INSERT INTO quest_master (quest_id, title, exp_gain, gold_gain, quest_type, target_user) "
            "VALUES (901, 'テストクエスト', 10, 100, 'daily', 'all')"
        )


def _latest_history_id() -> int:
    with common.get_db_cursor() as cur:
        return cur.execute("SELECT id FROM quest_history ORDER BY id DESC LIMIT 1").fetchone()["id"]


def _gold() -> int:
    with common.get_db_cursor() as cur:
        return cur.execute("SELECT gold FROM quest_users WHERE user_id='dad'").fetchone()["gold"]


def test_cancel_after_spending_reward_is_rejected(isolated_db, monkeypatch):
    """付与ゴールドを消費済み(残高 < 付与額)の履歴はキャンセルできない。"""
    monkeypatch.setattr(qs_module.notification_service, "send_push", lambda *a, **k: True)
    monkeypatch.setattr(qs_module.sound_manager, "play", lambda *a, **k: None)
    _seed(gold=0)
    service = qs_module.QuestService()

    service.process_complete_quest("dad", 901)
    assert _gold() == 100
    hist_id = _latest_history_id()

    # 報酬購入相当: 残高を直接消費する
    with common.get_db_cursor(commit=True) as cur:
        cur.execute("UPDATE quest_users SET gold = 0 WHERE user_id='dad'")

    with pytest.raises(HTTPException) as exc:
        service.process_cancel_quest("dad", hist_id)
    assert exc.value.status_code == 400

    # 履歴も残高もそのまま(トランザクションが巻き戻されている)
    assert _gold() == 0
    with common.get_db_cursor() as cur:
        assert cur.execute("SELECT 1 FROM quest_history WHERE id=?", (hist_id,)).fetchone() is not None

    # 再完了も「本日完了済み」として拒否される = 無限ゴールドが成立しない
    with pytest.raises(HTTPException):
        service.process_complete_quest("dad", 901)
    assert _gold() == 0


def test_cancel_with_sufficient_balance_fully_reverts(isolated_db, monkeypatch):
    """残高が付与額以上なら従来どおり付与分をきっちり戻す(飽和なし)。"""
    monkeypatch.setattr(qs_module.notification_service, "send_push", lambda *a, **k: True)
    monkeypatch.setattr(qs_module.sound_manager, "play", lambda *a, **k: None)
    _seed(gold=30)
    service = qs_module.QuestService()

    service.process_complete_quest("dad", 901)
    assert _gold() == 130
    hist_id = _latest_history_id()

    service.process_cancel_quest("dad", hist_id)
    assert _gold() == 30
    with common.get_db_cursor() as cur:
        assert cur.execute("SELECT 1 FROM quest_history WHERE id=?", (hist_id,)).fetchone() is None
