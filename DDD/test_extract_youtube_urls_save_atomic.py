# DDD/test_extract_youtube_urls_save_atomic.py
"""
Issue #413 (D-L10) の回帰テスト。

FileManager.save()は以前、output_path.open("w", ...)で保存先ファイルへ直接
書き込んでいた。NAS瞬断等で書き込み中にプロセスが中断すると、同名ファイルが
既に存在するケース（チャンネル名/タイトルの重複）では中身が空/一部だけの
壊れたファイルで上書きされたまま残ってしまいうった。

本テストは、
    1. 保存が成功した場合、一時ファイル(.tmp)が残らず本来のファイルへ
       置き換わっていること
    2. 書き込み中に失敗した場合、一時ファイルが残置されず、既存の
       (保存前の)ファイルの内容が破損せずそのまま残ること
を検証する。

DDDにはpytest基盤(conftest.py等)が無いため、本ファイルは
`pytest DDD/test_extract_youtube_urls_save_atomic.py` のように直接指定して
実行する(MY_HOME_SYSTEM/pytest.ini の testpaths=tests のスコープ外)。
"""
import sys
from pathlib import Path

DDD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DDD_DIR))

import extract_youtube_urls as module  # noqa: E402


def _make_result(title="video", urls=None):
    return module.ExtractionResult(
        title=title, urls=urls or ["https://www.youtube.com/watch?v=1"],
        source_url="https://www.youtube.com/@test_channel",
    )


class TestSaveIsAtomicViaTmpAndReplace:
    def test_successful_save_leaves_no_tmp_file(self, tmp_path):
        manager = module.FileManager()
        result = _make_result()

        saved = manager.save(result, base_dir=tmp_path)

        assert saved is True
        output_path = tmp_path / module.AppConfig.SUB_DIR_NAME / "video.txt"
        tmp_file = output_path.with_suffix(output_path.suffix + ".tmp")
        assert output_path.exists()
        assert not tmp_file.exists()
        assert output_path.read_text(encoding="utf-8") == "https://www.youtube.com/watch?v=1\n"

    def test_write_failure_does_not_corrupt_existing_file_or_leave_tmp(self, tmp_path, monkeypatch):
        manager = module.FileManager()
        first = _make_result(title="video", urls=["https://www.youtube.com/watch?v=original"])
        assert manager.save(first, base_dir=tmp_path) is True

        output_path = tmp_path / module.AppConfig.SUB_DIR_NAME / "video.txt"
        original_content = output_path.read_text(encoding="utf-8")
        tmp_file = output_path.with_suffix(output_path.suffix + ".tmp")

        original_open = Path.open

        def _raise_on_tmp_write(self, *args, **kwargs):
            if self == tmp_file:
                raise IOError("simulated write failure")
            return original_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", _raise_on_tmp_write)

        second = _make_result(title="video", urls=["https://www.youtube.com/watch?v=new"])
        saved = manager.save(second, base_dir=tmp_path)

        assert saved is False
        # 一時ファイルが残置されないこと
        assert not tmp_file.exists()
        # 既存ファイルは破損せず、保存前の内容のまま残ること
        assert output_path.read_text(encoding="utf-8") == original_content


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
