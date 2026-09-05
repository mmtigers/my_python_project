# DDD/test_split_prompts.py
"""
Issue #244の回帰テスト。

split_prompts()は、入力Markdown中に「番号」「タイトル」の組が複数回出現し、
ゼロ埋め後の番号とサニタイズ後タイトルの組み合わせが同一ファイル名に解決する
場合、以前は警告ログを出すのみで実際には無条件に上書きしていた。これにより、
先に書き出した項目のPrompt内容が後続の項目によって完全に上書きされ、
分割結果から消失していた。

本テストは、
    1. 同一実行内で2つの項目が同じファイル名に解決する場合、両方の項目が
       別々のファイルとして保存され、どちらの内容も失われないこと
    2. 衝突が無い通常のケースでは従来通りの単純なファイル名で保存されること
    3. 出力先ディレクトリに前回実行分の同名ファイルが既に存在する場合は
       (同一実行内の衝突とは異なり)従来通り上書きされること
を検証する。

DDDにはpytest基盤(conftest.py等)が無いため、本ファイルは
`pytest DDD/test_split_prompts.py` のように直接指定して実行する
(MY_HOME_SYSTEM/pytest.ini の testpaths=tests のスコープ外)。
"""
import sys
from pathlib import Path

DDD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DDD_DIR))

from split_prompts import split_prompts  # noqa: E402


def test_duplicate_number_and_title_preserves_both_items(tmp_path):
    """#244: 同一実行内で番号/タイトルの組が重複しても、両方の項目内容が保存されること。"""
    input_file = tmp_path / "input.md"
    input_file.write_text(
        "1. サンプルタイトル\n"
        "\n"
        "Prompt: 最初の内容\n"
        "\n"
        "1. サンプルタイトル\n"
        "\n"
        "Prompt: 2番目の内容(別データだが同じ番号/タイトル)\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "out"

    written = split_prompts(input_file, output_dir)

    assert written == 2
    md_files = sorted(output_dir.glob("*.md"))
    assert len(md_files) == 2, "両方の項目が別々のファイルとして保存されるべき"

    contents = [f.read_text(encoding="utf-8") for f in md_files]
    assert any("最初の内容" in c for c in contents)
    assert any("2番目の内容" in c for c in contents), "後続の項目内容が上書きで失われてはならない"


def test_no_duplicates_uses_plain_filenames(tmp_path):
    """回帰防止: 衝突が無い通常ケースでは連番サフィックス無しのファイル名になること。"""
    input_file = tmp_path / "input.md"
    input_file.write_text(
        "1. タイトルA\n\nPrompt: 内容A\n\n2. タイトルB\n\nPrompt: 内容B\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "out"

    written = split_prompts(input_file, output_dir)

    assert written == 2
    filenames = sorted(f.name for f in output_dir.glob("*.md"))
    assert filenames == ["01_タイトルA.md", "02_タイトルB.md"]


def test_preexisting_file_from_previous_run_is_still_overwritten(tmp_path):
    """回帰防止: 前回実行分の同名ファイルが既に存在する場合は従来通り上書きされること
    (同一実行内での重複とは異なり、再実行時の意図的な上書きは維持する)。"""
    input_file = tmp_path / "input.md"
    input_file.write_text("1. タイトルA\n\nPrompt: 新しい内容\n", encoding="utf-8")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "01_タイトルA.md").write_text("# 古い内容\n", encoding="utf-8")

    written = split_prompts(input_file, output_dir)

    assert written == 1
    md_files = list(output_dir.glob("*.md"))
    assert len(md_files) == 1
    assert "新しい内容" in md_files[0].read_text(encoding="utf-8")


def test_item_without_prompt_line_is_warned_and_skipped(tmp_path, caplog):
    """#468: 「番号. タイトル」に続くPrompt行が無い等でフォーマットに一致しない
    項目は、無警告でスキップされず警告ログに記録されること。"""
    input_file = tmp_path / "input.md"
    input_file.write_text(
        "1. 正常な項目\n\nPrompt: これは保存される\n\n"
        "2. Prompt行が無い項目\n\n"
        "3. また別の正常な項目\n\nPrompt: これも保存される\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "out"

    with caplog.at_level("WARNING"):
        written = split_prompts(input_file, output_dir)

    assert written == 2
    assert any("2" in record.message and "フォーマット" in record.message for record in caplog.records)


def test_fully_matching_input_does_not_warn_about_format(tmp_path, caplog):
    """全項目がフォーマットに一致する場合、フォーマット不一致の警告は出ないこと。"""
    input_file = tmp_path / "input.md"
    input_file.write_text("1. タイトルA\n\nPrompt: 内容A\n", encoding="utf-8")
    output_dir = tmp_path / "out"

    with caplog.at_level("WARNING"):
        split_prompts(input_file, output_dir)

    assert not any("フォーマット" in record.message for record in caplog.records)


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
