from pathlib import Path

# 対象ディレクトリ（必要に応じてパスを変更してください。デフォルトはカレントディレクトリ）
target_dir = "."
output_file = "merged_docs.txt"

# Low: 除外なしで rglob すると .git/node_modules/venv 等の対象外ディレクトリまで
# 巻き込んでしまう上、Path.rglob() が返す順序はOS/ファイルシステム依存で
# 非決定的(実行のたびに結合順が変わりうる)だった。
EXCLUDE_DIR_NAMES = {
    ".git", "node_modules", "venv", ".venv", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".hypothesis", "dist", "build",
}


def find_markdown_files(base_dir) -> list:
    """base_dir配下の.mdファイルを、除外ディレクトリを除いてパス文字列昇順(決定的)で返す。"""
    base = Path(base_dir)
    files = [
        p for p in base.rglob("*.md")
        if not EXCLUDE_DIR_NAMES.intersection(p.parts)
    ]
    return sorted(files, key=lambda p: str(p))


def merge_markdown_files(base_dir, output_path) -> None:
    with open(output_path, "w", encoding="utf-8") as outfile:
        for filepath in find_markdown_files(base_dir):
            # ファイルの境界を明確にするセパレーター
            outfile.write(f"\n\n{'='*50}\n")
            outfile.write(f"File Path: {filepath}\n")
            outfile.write(f"{'='*50}\n\n")

            try:
                with open(filepath, "r", encoding="utf-8") as infile:
                    outfile.write(infile.read())
            except Exception as e:
                outfile.write(f"[Error reading file: {e}]\n")


if __name__ == "__main__":
    merge_markdown_files(target_dir, output_file)
    print(f"すべてのMarkdownファイルを {output_file} に結合しました。")
