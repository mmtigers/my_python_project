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
残件対応(M-1-3)でユーザーへ確認のうえ「1周期(daily/weekly)につき1回に制限する」
ことが確定した。'infinite' タイプ(「何回でも挑戦しよう」等、仕様上多重完了が
前提のクエスト)は対象外とする。下記 TestRepeatedCompletionAfterGuardWindow /
TestResetPeriodEnforcement を参照。
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
            "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain, reset_period) VALUES (?, ?, ?, ?, ?, ?)",
            (quest_id, "TestQuest", "daily", 10, 5, "daily"),
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

        'infinite' タイプ(M-1-3のサーバー側周期リセット強制の対象外)を使い、
        このテストが検証したい「10秒スパムガードの境界判定」を、M-1-3で新設した
        「周期内の再完了禁止」チェックと分離する。
        """
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold) VALUES (?, ?, ?, ?, ?, ?)",
                ("dad", "Test", "Warrior", 1, 0, 0),
            )
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain) VALUES (?, ?, ?, ?, ?)",
                (9001, "TestQuest", "infinite", 10, 5),
            )
        quest_service = QuestService()
        quest_service.process_complete_quest("dad", 9001)
        _set_last_completed_at("dad", 9001, seconds_ago=10.5)

        result = quest_service.process_complete_quest("dad", 9001)
        assert result["status"] == "success"


class TestRepeatedCompletionAfterGuardWindow:
    """
    M-1-3: 10秒ガードを超えた「本当のリトライ」であっても、同一周期(daily)内の
    再完了はサーバー側で拒否され、報酬は多重加算されないことの回帰テスト。

    修正前は10秒スパムチェックしか無く、is_within_reset_period は表示用途
    (completedQuests算出)にしか使われていなかったため、API直叩き等で同一dailyクエストを
    1日に何度でも完了・多重報酬できてしまっていた。
    """

    def test_completion_after_guard_window_is_rejected_same_day(self, isolated_db):
        _seed_quest()
        quest_service = QuestService()
        first = quest_service.process_complete_quest("dad", 9001)
        assert first["earnedGold"] == 5

        _set_last_completed_at("dad", 9001, seconds_ago=11)

        with pytest.raises(HTTPException) as exc_info:
            quest_service.process_complete_quest("dad", 9001)
        assert exc_info.value.status_code == 400

        with common.get_db_cursor() as cur:
            user = cur.execute("SELECT gold FROM quest_users WHERE user_id = 'dad'").fetchone()
        assert user["gold"] == 5  # 二重加算されていない


class TestResetPeriodEnforcement:
    """M-1-3: reset_period に応じたサーバー側の完了回数制限。"""

    def test_daily_quest_can_be_completed_again_next_day(self, isolated_db):
        _seed_quest()
        quest_service = QuestService()
        quest_service.process_complete_quest("dad", 9001)
        # 前日に完了したことにする -> 本日はまだ未完了のはず
        _set_last_completed_at("dad", 9001, seconds_ago=25 * 3600)

        result = quest_service.process_complete_quest("dad", 9001)
        assert result["status"] == "success"

    def test_weekly_quest_rejected_within_same_week(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold) VALUES (?, ?, ?, ?, ?, ?)",
                ("dad", "Test", "Warrior", 1, 0, 0),
            )
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain, reset_period) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (9002, "WeeklyQuest", "daily", 10, 5, "weekly"),
            )
        quest_service = QuestService()
        quest_service.process_complete_quest("dad", 9002)
        # 固定の「2日前」だと実行日が週の月・火曜の場合に前週へまたいでしまい
        # 週境界をランダムに踏んでflakyになるため、必ず「今週の月曜0時」を
        # 完了日時とすることで実行日に関わらず「今週内」を保証する。
        now_jst = datetime.datetime.now(JST)
        monday_this_week_start = (now_jst - datetime.timedelta(days=now_jst.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        seconds_ago = max(0, (now_jst - monday_this_week_start).total_seconds())
        _set_last_completed_at("dad", 9002, seconds_ago=seconds_ago)

        with pytest.raises(HTTPException) as exc_info:
            quest_service.process_complete_quest("dad", 9002)
        assert exc_info.value.status_code == 400

    def test_infinite_quest_type_is_exempt_from_reset_period(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold) VALUES (?, ?, ?, ?, ?, ?)",
                ("dad", "Test", "Warrior", 1, 0, 0),
            )
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain) VALUES (?, ?, ?, ?, ?)",
                (9003, "InfiniteQuest", "infinite", 10, 5),
            )
        quest_service = QuestService()
        quest_service.process_complete_quest("dad", 9003)
        _set_last_completed_at("dad", 9003, seconds_ago=11)

        result = quest_service.process_complete_quest("dad", 9003)
        assert result["status"] == "success"
