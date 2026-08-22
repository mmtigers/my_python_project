# scripts/test_merge_mds.py
"""
scripts/merge_mds.py のテスト。

Low: rglob("*.md") が .git/node_modules/venv 等の対象外ディレクトリまで巻き込む上、
返す順序がOS/ファイルシステム依存で非決定的だった(出力ファイルの内容・順序が
実行のたびに変わりうる)ことの回帰テスト。
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.dirname(__file__)))

import merge_mds


def _touch(path, content="# doc\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestFindMarkdownFilesExcludesVendorDirs:
    def test_excludes_git_node_modules_and_venv(self, tmp_path):
        _touch(tmp_path / "README.md")
        _touch(tmp_path / "docs" / "guide.md")
        _touch(tmp_path / ".git" / "COMMIT_EDITMSG.md")  # 実際には.md拡張子はまず無いが境界値として
        _touch(tmp_path / "node_modules" / "some-pkg" / "README.md")
        _touch(tmp_path / "venv" / "lib" / "site-packages" / "pkg" / "README.md")
        _touch(tmp_path / ".venv" / "lib" / "README.md")

        found = merge_mds.find_markdown_files(tmp_path)
        found_relative = sorted(str(p.relative_to(tmp_path)) for p in found)

        assert found_relative == sorted([
            os.path.join("docs", "guide.md"),
            "README.md",
        ])


class TestFindMarkdownFilesIsDeterministic:
    def test_result_is_sorted_by_path(self, tmp_path):
        _touch(tmp_path / "zzz.md")
        _touch(tmp_path / "aaa.md")
        _touch(tmp_path / "mmm" / "nested.md")

        found = merge_mds.find_markdown_files(tmp_path)
        found_str = [str(p) for p in found]

        assert found_str == sorted(found_str)


class TestMergeMarkdownFiles:
    def test_writes_all_files_in_deterministic_order(self, tmp_path):
        _touch(tmp_path / "b.md", "content-b")
        _touch(tmp_path / "a.md", "content-a")
        output_path = tmp_path / "out.txt"

        merge_mds.merge_markdown_files(tmp_path, output_path)

        content = output_path.read_text(encoding="utf-8")
        assert content.index("a.md") < content.index("b.md")
        assert "content-a" in content
        assert "content-b" in content
