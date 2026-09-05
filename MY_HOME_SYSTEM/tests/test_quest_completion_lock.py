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


def test_same_key_serializes_sequential_acquisitions_and_cleans_up_afterward():
    """#435: 参照カウント付きレジストリ化後も、同一キーへの取得は正しく
    直列化でき(再入時にデッドロックしない)、使用後はレジストリから
    エントリが自動的に削除される(キーが増え続けても無制限に肥大化しない)。"""
    key = ("user1", 101)
    with quest_service._get_completion_lock(key):
        pass
    assert key not in quest_service._completion_locks

    with quest_service._get_completion_lock(key):
        pass
    assert key not in quest_service._completion_locks


def test_different_keys_do_not_block_each_other():
    key_a = ("user1", 101)
    key_b = ("user1", 102)
    a_acquired = threading.Event()
    release_a = threading.Event()

    def hold_a():
        with quest_service._get_completion_lock(key_a):
            a_acquired.set()
            release_a.wait(timeout=5)

    t = threading.Thread(target=hold_a)
    t.start()
    assert a_acquired.wait(timeout=5)

    try:
        start = time.monotonic()
        with quest_service._get_completion_lock(key_b):
            elapsed = time.monotonic() - start
    finally:
        release_a.set()
        t.join(timeout=5)

    # key_a のロック保持中でも、別キーの key_b は待たされず即座に取得できる
    assert elapsed < 1.0


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
