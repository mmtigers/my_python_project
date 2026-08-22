# DDD/test_file_utils.py
"""
file_utils.sanitize_filename() の回帰テスト。

DDDにはpytest基盤(conftest.py等)が無いため、本ファイルは
`pytest DDD/test_file_utils.py` のように直接指定して実行する
(MY_HOME_SYSTEM/pytest.ini の testpaths=tests のスコープ外)。

Low: sanitize_filename は入力が ".." や "." 等の記号のみで構成されている場合、
.strip('. ') によって空文字列を返してしまう。呼び出し側(例:
batch_download_discord.py:513 の `sanitize_filename(video_id) + ".mp4"`)は
戻り値へ拡張子を連結するだけなので、空文字が返ると ".mp4" のような
隠しファイル(空stem)が生成されうる。パストラバーサル自体は成立しない
(空文字になる時点で ".." という文字列そのものは失われるため)が、
生成物として意図しない隠しファイル・空ファイル名は避けるべき。
"""
import sys
from pathlib import Path

import pytest

DDD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DDD_DIR))

from file_utils import sanitize_filename  # noqa: E402


class TestSanitizeFilenameBasicBehavior:
    def test_replaces_forbidden_characters(self):
        assert sanitize_filename('a/b\\c*d?e:f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"

    def test_truncates_to_max_length(self):
        result = sanitize_filename("a" * 300, max_length=200)
        assert len(result) == 200


class TestSanitizeFilenameNeverReturnsEmptyString:
    @pytest.mark.parametrize("degenerate_input", ["..", ".", "...", "   ", ". . ."])
    def test_symbol_only_input_does_not_produce_empty_string(self, degenerate_input):
        result = sanitize_filename(degenerate_input)
        assert result != "", (
            f"sanitize_filename({degenerate_input!r}) returned an empty string; "
            "callers that append an extension (e.g. result + '.mp4') would then "
            "produce a hidden dotfile with an empty stem"
        )

    def test_caller_pattern_does_not_produce_a_hidden_dotfile(self):
        """batch_download_discord.py:513 の `sanitize_filename(video_id) + '.mp4'` を再現。"""
        video_id = ".."
        filename = sanitize_filename(video_id) + ".mp4"
        assert not filename.startswith("."), (
            f"generated filename {filename!r} is a hidden dotfile with an empty stem"
        )
