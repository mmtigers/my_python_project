# MY_HOME_SYSTEM/tests/test_quest_tab_html_escaping.py
"""
views/dashboard/quest_tab.py の unsafe_allow_html=True 埋め込み箇所(達成履歴 log['text'])が、
DB由来の文字列(クエストタイトル等。認証なしの/api/questから自由に書き込める)を
HTMLエスケープしてから埋め込んでいることの回帰テスト。
(Issue #378: 格納型HTMLインジェクション)
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from views.dashboard import quest_tab

XSS_PAYLOAD = "<img src=x onerror=alert(1)>"


def _mock_st():
    mock = MagicMock()
    # st.columns はユーザー数分(int)とレイアウト比率(list)の2種類の引数で呼ばれるため、
    # 引数に応じた個数のMagicMockを返す。
    mock.columns.side_effect = lambda n: [MagicMock() for _ in range(n if isinstance(n, int) else len(n))]
    return mock


def _rendered_html(mock_markdown) -> str:
    return "\n".join(str(call.args[0]) for call in mock_markdown.call_args_list)


def _fake_view_data(log_text):
    return {
        "users": [{"name": "太郎", "job_class": "Warrior", "exp": 10, "gold": 5}],
        "logs": [{"text": log_text, "timestamp": "2026-09-04 10:00"}],
    }


def test_render_escapes_malicious_quest_log_text():
    malicious_text = f"太郎 が「{XSS_PAYLOAD}」を達成"
    mock_st = _mock_st()
    with patch.object(quest_tab, "st", mock_st), \
         patch.object(quest_tab.game_system, "get_all_view_data", return_value=_fake_view_data(malicious_text)):
        quest_tab.render()

    html_out = _rendered_html(mock_st.markdown)
    assert XSS_PAYLOAD not in html_out
    assert "&lt;img src=x onerror=alert(1)&gt;" in html_out


def test_render_still_shows_plain_log_text_normally():
    mock_st = _mock_st()
    with patch.object(quest_tab, "st", mock_st), \
         patch.object(quest_tab.game_system, "get_all_view_data", return_value=_fake_view_data("太郎がお皿洗いを達成")):
        quest_tab.render()

    html_out = _rendered_html(mock_st.markdown)
    assert "太郎がお皿洗いを達成" in html_out
