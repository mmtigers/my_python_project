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


class TestSanitizeFilenameByteSafeTruncation:
    """Issue #175の回帰テスト: 以前はsafe[:max_length]で「文字数」を制限しており、
    UTF-8で1文字3バイトになる日本語では200文字(最大600バイト)がext4等の255バイト
    上限を容易に超過し、ファイル操作がENAMETOOLONGで失敗しうる不具合があった。"""

    def test_japanese_title_result_stays_within_byte_limit(self):
        # 「あ」は3バイト。200文字なら文字数ベースの旧実装では600バイトになり
        # 255バイトを大幅に超過していた。
        result = sanitize_filename("あ" * 200, max_length=200)
        assert len(result.encode("utf-8")) <= 200

    def test_japanese_title_is_not_truncated_mid_character(self):
        """マルチバイト文字の境界で切り詰めても、不完全なバイト列による
        UnicodeDecodeErrorや文字化けを起こさず正しくデコードできること。"""
        # 「あ」(3バイト)を101個 = 303バイト。max_length=100バイトで切ると
        # 単純なバイトスライスでは33文字目の途中(3バイト目)で切断される。
        result = sanitize_filename("あ" * 101, max_length=100)
        # 全て正しく再エンコードできる(不完全なバイト列が残っていない)こと
        assert result.encode("utf-8").decode("utf-8") == result
        assert len(result.encode("utf-8")) <= 100
        # 33文字(99バイト)までは安全に残るはず
        assert result == "あ" * 33

    def test_ascii_only_behavior_is_unchanged(self):
        """既存のASCII入力に対する挙動(文字数=バイト数)は変わらないこと。"""
        result = sanitize_filename("a" * 300, max_length=200)
        assert result == "a" * 200


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
