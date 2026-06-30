import os
from pathlib import Path

# 対象ディレクトリ（必要に応じてパスを変更してください。デフォルトはカレントディレクトリ）
target_dir = "."
output_file = "merged_docs.txt"

with open(output_file, "w", encoding="utf-8") as outfile:
    for filepath in Path(target_dir).rglob("*.md"):
        # ファイルの境界を明確にするセパレーター
        outfile.write(f"\n\n{'='*50}\n")
        outfile.write(f"File Path: {filepath}\n")
        outfile.write(f"{'='*50}\n\n")
        
        try:
            with open(filepath, "r", encoding="utf-8") as infile:
                outfile.write(infile.read())
        except Exception as e:
            outfile.write(f"[Error reading file: {e}]\n")

print(f"すべてのMarkdownファイルを {output_file} に結合しました。")