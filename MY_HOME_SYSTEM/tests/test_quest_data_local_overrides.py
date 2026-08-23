# MY_HOME_SYSTEM/tests/test_quest_data_local_overrides.py
"""
M-9-1: quest_data.py の USERS[].info に実年齢・住宅ローン残高等の個人情報が
ハードコードされていた問題の回帰テスト。

config.py の FAMILY_SETTINGS (family_members.local.json) と同じ方針で、
tracked source (quest_data.py) 側はプレースホルダーのみを持ち、gitignore対象の
quest_users.local.json が存在すればそこから info を user_id 単位で上書きする。
ファイルが無くても (CI・新規チェックアウト等) プレースホルダーのまま import できる
ことも確認する。
"""
import importlib
import json
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import quest_data


def test_tracked_source_does_not_hardcode_pii():
    """
    tracked source 上の USERS[].info には、実年齢や具体的な金額など
    個人を特定しうる情報が直接埋め込まれていないこと。
    """
    reloaded = importlib.reload(quest_data)
    for user in reloaded.USERS:
        info = user.get('info', '')
        assert '歳' not in info, f"{user['user_id']} の info に年齢が含まれています: {info}"
        assert '万' not in info, f"{user['user_id']} の info に金額が含まれています: {info}"


def test_missing_local_file_falls_back_to_placeholder(monkeypatch, tmp_path):
    """
    quest_users.local.json が存在しない環境でも、プレースホルダーのままで
    問題なく import できる (CI・新規チェックアウト等)。
    """
    monkeypatch.setenv("QUEST_USERS_LOCAL_PATH", str(tmp_path / "does-not-exist.json"))
    reloaded = importlib.reload(quest_data)
    try:
        assert reloaded.USERS, "USERS が空になってはいけない"
        dad = next(u for u in reloaded.USERS if u['user_id'] == 'dad')
        assert '歳' not in dad['info']
    finally:
        monkeypatch.undo()
        importlib.reload(quest_data)


def test_local_override_file_merges_into_users(monkeypatch, tmp_path):
    """
    quest_users.local.json が存在する場合、user_id をキーに info が上書きされる。
    """
    local_path = tmp_path / "quest_users.local.json"
    local_path.write_text(
        json.dumps({"dad": {"info": "35歳 / テスト用の実データ"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("QUEST_USERS_LOCAL_PATH", str(local_path))
    reloaded = importlib.reload(quest_data)
    try:
        dad = next(u for u in reloaded.USERS if u['user_id'] == 'dad')
        assert dad['info'] == "35歳 / テスト用の実データ"
        # 上書き対象外のユーザーはプレースホルダーのまま
        mom = next(u for u in reloaded.USERS if u['user_id'] == 'mom')
        assert '歳' not in mom['info']
    finally:
        monkeypatch.undo()
        importlib.reload(quest_data)
