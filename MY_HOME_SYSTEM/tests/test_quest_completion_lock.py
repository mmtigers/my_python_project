# MY_HOME_SYSTEM/tests/test_quest_completion_lock.py
"""
services/quest_service.py の _get_completion_lock (二重加算防止用ロック) のテスト。

process_complete_quest は「直近履歴を読む→報酬を書く」という手順のため、
同一(user_id, quest_id)への同時リクエストが競合すると報酬が二重加算されうる
レースコンディションがあった。プロセス内ロックで直列化されることを確認する。
"""
import os
import sys
import threading
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services import quest_service


def test_same_key_returns_same_lock_instance():
    key = ("user1", 101)
    lock_a = quest_service._get_completion_lock(key)
    lock_b = quest_service._get_completion_lock(key)
    assert lock_a is lock_b


def test_different_key_returns_different_lock_instance():
    lock_a = quest_service._get_completion_lock(("user1", 101))
    lock_b = quest_service._get_completion_lock(("user1", 102))
    assert lock_a is not lock_b


def test_concurrent_access_to_same_key_is_serialized():
    key = ("user_race", 999)
    overlap_detected = {"value": False}
    in_critical_section = {"value": False}

    def critical_section():
        with quest_service._get_completion_lock(key):
            if in_critical_section["value"]:
                overlap_detected["value"] = True
            in_critical_section["value"] = True
            time.sleep(0.05)
            in_critical_section["value"] = False

    threads = [threading.Thread(target=critical_section) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert overlap_detected["value"] is False
