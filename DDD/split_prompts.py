#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Prompt List Splitter
---------------------
「番号. タイトル」+「Prompt: 内容」形式で列挙されたMarkdownファイルを、
項目ごとの個別Markdownファイルへ分割するスクリプト。
"""

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import List, Tuple

from file_utils import sanitize_filename as _shared_sanitize_filename

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("SplitPrompts")

# 「番号. タイトル」と「Prompt: 内容」のブロックを抽出する正規表現。
# カテゴリのヘッダーなどは無視し、要件の箇所のみを的確にキャッチする。
PROMPT_PATTERN = re.compile(r'(\d+)\.\s+([^\n]+)\n+Prompt:\s+([^\n]+)')


def split_prompts(input_file: Path, output_dir: Path) -> int:
    """入力Markdownファイルを項目ごとの個別ファイルへ分割する。

    Args:
        input_file: 「番号. タイトル」「Prompt: 内容」形式を含む入力ファイル。
        output_dir: 分割結果を書き出す出力先ディレクトリ（存在しなければ作成する）。

    Returns:
        書き出したファイルの件数。

    Raises:
        FileNotFoundError: input_file が存在しない場合。
    """
    content = input_file.read_text(encoding='utf-8')

    matches: List[Tuple[str, str, str]] = PROMPT_PATTERN.findall(content)
    if not matches:
        logger.warning(
            f"⚠️ '{input_file}' から「番号. タイトル」「Prompt: 内容」形式の項目が見つかりませんでした。"
            "フォーマットを確認してください。"
        )
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)

    # 出現する番号の最大桁数に応じてゼロ埋め幅を動的に決定する（例: "1000"が
    # 出現するなら4桁）。固定2桁だと100番以降で "01" < "100" < "1000" < "23" の
    # ように文字列ソートが数値順と食い違う不具合が発生するため、項目「数」ではなく
    # 実際に出現する番号「文字列」の最大長を基準にする。
    pad_width = max(2, max(len(num_str) for num_str, _, _ in matches))

    written = 0
    for num_str, raw_title, prompt_text in matches:
        num = num_str.zfill(pad_width)
        raw_title = raw_title.strip()
        safe_title = _shared_sanitize_filename(raw_title)
        prompt_text = prompt_text.strip()

        filename = f"{num}_{safe_title}.md"
        filepath = output_dir / filename

        if filepath.exists():
            logger.warning(f"⚠️ 上書き: {filename} は既に存在します（元データの番号/タイトルが重複している可能性）")

        filepath.write_text(f"# {raw_title}\n\nPrompt: {prompt_text}\n", encoding='utf-8')
        written += 1

    logger.info(f"処理完了: '{output_dir}' フォルダ内に {written} 個のマークダウンファイルを作成しました。")
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Split a numbered prompt list Markdown file into individual files.")
    parser.add_argument(
        "input_file", nargs="?",
        default="一ノ瀬蓮_プロンプト1000選.md",
        help="Input Markdown file (default: %(default)s)"
    )
    parser.add_argument(
        "output_dir", nargs="?",
        default="split_results",
        help="Output directory (default: %(default)s)"
    )
    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.exists():
        logger.error(f"❌ 入力ファイルが見つかりません: {input_path}")
        sys.exit(1)

    split_prompts(input_path, Path(args.output_dir))


if __name__ == "__main__":
    main()
