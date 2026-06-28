import re
import os

def split_prompts(input_file, output_dir):
    # 読み込み
    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # 正規表現で「番号. タイトル」と「Prompt: 内容」のブロックを抽出
    # カテゴリのヘッダーなどは無視し、要件の箇所のみを的確にキャッチします
    pattern = re.compile(r'(\d+)\.\s+([^\n]+)\n+Prompt:\s+([^\n]+)')
    matches = pattern.findall(content)

    # 出力先ディレクトリの作成
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    for match in matches:
        # 1〜9を 01, 02 のようにゼロ埋めしてソートしやすくする
        num = match[0].zfill(2)
        
        # タイトルからファイル名に使用できない禁則文字をアンダースコアに置換
        raw_title = match[1].strip()
        safe_title = re.sub(r'[\\/:*?"<>|]', '_', raw_title)
        
        prompt_text = match[2].strip()

        # ファイル名の定義 (例: 01_デスクで集中.md)
        filename = f"{num}_{safe_title}.md"
        filepath = os.path.join(output_dir, filename)

        # 個別ファイルの書き出し
        with open(filepath, 'w', encoding='utf-8') as out_f:
            out_f.write(f"# {raw_title}\n\n")
            out_f.write(f"Prompt: {prompt_text}\n")

    print(f"処理完了: '{output_dir}' フォルダ内に {len(matches)} 個のマークダウンファイルを作成しました。")

if __name__ == "__main__":
    # 入力ファイル名と出力フォルダ名の指定
    TARGET_FILE = '一ノ瀬蓮_プロンプト1000選.md'
    OUTPUT_FOLDER = 'split_results'
    
    split_prompts(TARGET_FILE, OUTPUT_FOLDER)