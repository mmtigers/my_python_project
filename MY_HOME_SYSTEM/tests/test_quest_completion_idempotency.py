# MY_HOME_SYSTEM/tests/test_quest_completion_idempotency.py
"""
services/quest_service.py の process_complete_quest における
「同一クエストへの再送信(リトライ・二重タップ)」時の挙動のテスト。

背景 (CODE_REVIEW_REPORT.md 4.2):
process_complete_quest は「直近10秒以内の同一クエスト完了はスパムとして429で拒否する」
というガードを持つ。実装調査の結果、このガードは以前
`datetime.datetime.now()` (サーバーのOSローカル時刻、素の実時間) と
DBに保存されたJST付きタイムスタンプ(tzinfoを剥がした後)を比較していたため、
サーバーのOSタイムゾーンがJST以外(GitHub ActionsのUTC等)だと実時間で10秒経過しても
差分が約9時間ズレたままとなり、**同一クエストが約9時間もの間 429 で完了できなくなる**
という重大な不具合があった(実際にこのテストファイルの作成過程で再現・確認した)。
この問題は services/quest_service.py 側で timezone-aware な比較に修正済み。

なお、CODE_REVIEW_REPORT.md 4.2 が本来指摘していた
「10秒を超えたリトライでは報酬が何度でも加算されてしまう」という論点(=同日内に
同じクエストを何度でも完了してよいかというビジネスルール)については、
コードからは意図を断定できず「仕様不明のため判断不能」。
このテストでは、そのビジネスルールを「バグ」として断定せず、
現状の挙動を記録するcharacterization testとして残す(下記
TestRepeatedCompletionAfterGuardWindow を参照)。
"""
import os
import sys
import datetime

import pytest
import pytz
from fastapi import HTTPException

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
from services.quest_service import QuestService

JST = pytz.timezone("Asia/Tokyo")


def _seed_quest(user_id: str = "dad", quest_id: int = 9001):
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, "Test", "Warrior", 1, 0, 0),
        )
        cur.execute(
            "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain) VALUES (?, ?, ?, ?, ?)",
            (quest_id, "TestQuest", "daily", 10, 5),
        )


def _set_last_completed_at(user_id: str, quest_id: int, seconds_ago: float) -> None:
    ts = (datetime.datetime.now(JST) - datetime.timedelta(seconds=seconds_ago)).isoformat()
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE quest_history SET completed_at = ? WHERE user_id = ? AND quest_id = ?",
            (ts, user_id, quest_id),
        )


class TestSpamGuardIsTimezoneSafe:
    """
    直近10秒以内の再送信は常に429、10秒を超えたら常に成功することを、
    サーバーのOSタイムゾーンに関係なく保証する回帰テスト。
    (このテスト自体はCI実行環境のタイムゾーン設定に依存しない — 内部でJSTタイムスタンプを
    直接DBに書き込んで検証するため)
    """

    def test_immediate_retry_within_10_seconds_is_rejected(self, isolated_db):
        _seed_quest()
        quest_service = QuestService()
        quest_service.process_complete_quest("dad", 9001)

        with pytest.raises(HTTPException) as exc_info:
            quest_service.process_complete_quest("dad", 9001)
        assert exc_info.value.status_code == 429

    def test_retry_just_under_10_seconds_is_still_rejected(self, isolated_db):
        _seed_quest()
        quest_service = QuestService()
        quest_service.process_complete_quest("dad", 9001)
        _set_last_completed_at("dad", 9001, seconds_ago=9.5)

        with pytest.raises(HTTPException) as exc_info:
            quest_service.process_complete_quest("dad", 9001)
        assert exc_info.value.status_code == 429

    def test_retry_just_over_10_seconds_succeeds_regardless_of_server_timezone(self, isolated_db):
        """
        回帰対象のバグそのもの: 修正前は、この呼び出しがサーバーのOSタイムゾーンが
        JST以外(UTC等)の場合に約9時間ぶんズレて誤って429を返し続けていた。
        """
        _seed_quest()
        quest_service = QuestService()
        quest_service.process_complete_quest("dad", 9001)
        _set_last_completed_at("dad", 9001, seconds_ago=10.5)

        result = quest_service.process_complete_quest("dad", 9001)
        assert result["status"] == "success"


class TestRepeatedCompletionAfterGuardWindow:
    """
    仕様不明のため判断不能: 10秒ガードを超えた「本当のリトライ」で、
    同一クエストの報酬が(意図通りかはコードから断定できないが)再度加算される
    現状の挙動を記録するcharacterization test。
    このテストが失敗する = 挙動が変わった、という検知のみを目的とし、
    「これが正しい仕様である」とは主張しない。
    """

    def test_completion_after_guard_window_grants_reward_again(self, isolated_db):
        _seed_quest()
        quest_service = QuestService()
        first = quest_service.process_complete_quest("dad", 9001)
        assert first["earnedGold"] == 5

        _set_last_completed_at("dad", 9001, seconds_ago=11)

        second = quest_service.process_complete_quest("dad", 9001)

        # 現状の実装では、同日内であっても再度報酬が加算される。
        # (これが意図的な「何度でもこなせるお手伝い」なのか、防ぐべき二重加算なのかは
        #  ビジネスルールとしてコードから断定できないため、最終報告書の
        #  「残っているリスク」に明記する)
        assert second["status"] == "success"
        assert second["earnedGold"] == 5

        with common.get_db_cursor() as cur:
            user = cur.execute("SELECT gold FROM quest_users WHERE user_id = 'dad'").fetchone()
        assert user["gold"] == 10  # 5 + 5: 二重加算されている
