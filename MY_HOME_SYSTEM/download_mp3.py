import os
import sys
import shutil
from typing import Optional

# 外部ライブラリのインポートチェック
try:
    import yt_dlp
except ImportError:
    print("エラー: 'yt-dlp' がインストールされていません。")
    print("実行コマンド: pip install yt-dlp")
    sys.exit(1)

# 指定されたデフォルトの保存先パス
DEFAULT_OUTPUT_DIR = "/home/masahiro/develop/family-quest/src/assets/sounds"


def check_ffmpeg_installed() -> bool:
    """FFmpegがシステムパスに含まれているかを確認する"""
    return shutil.which("ffmpeg") is not None


def download_audio_as_mp3(youtube_url: str, output_dir: str, custom_filename: Optional[str] = None) -> None:
    """
    YouTube URLから音声をダウンロードし、MP3に変換して保存する。

    Args:
        youtube_url (str): 対象のYouTube動画URL
        output_dir (str): 保存先のディレクトリパス
        custom_filename (Optional[str]): ユーザー指定のファイル名（拡張子なし）。Noneの場合は動画タイトルを使用。
    """
    # 保存先ディレクトリの作成（再帰的に作成、存在していてもOK）
    if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir, exist_ok=True)
            print(f"[Info] ディレクトリを作成しました: {output_dir}")
        except OSError as e:
            print(f"[Error] ディレクトリ作成に失敗しました: {e}")
            return

    # ファイル名テンプレートの決定
    if custom_filename:
        # ユーザー指定名がある場合: output_dir/指定名.拡張子
        # ユーザーが誤って拡張子をつけていた場合、それを除去して二重拡張子を防ぐ
        base_name = os.path.splitext(custom_filename)[0]
        outtmpl = f'{output_dir}/{base_name}.%(ext)s'
        print(f"[Info] 指定されたファイル名で保存します: {base_name}.mp3")
    else:
        # 指定がない場合: output_dir/動画タイトル.拡張子
        outtmpl = f'{output_dir}/%(title)s.%(ext)s'
        print(f"[Info] 動画タイトルをファイル名として保存します。")

    # yt-dlpの設定
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': outtmpl,
        'restrictfilenames': True if not custom_filename else False,  # 自動タイトルの場合はファイル名を安全な文字に制限
        'noplaylist': True,
        'writethumbnail': True,
        'postprocessors': [
            {
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            },
            {
                'key': 'EmbedThumbnail',
            },
            {
                'key': 'FFmpegMetadata',
            },
        ],
        'clean_infojson': True,
        'quiet': False,
        'no_warnings': True,
    }

    print(f"\n[Start] ダウンロードを開始します: {youtube_url}")
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([youtube_url])
            print(f"\n[Success] 完了しました！")
            print(f"保存フォルダ: {output_dir}")

    except yt_dlp.utils.DownloadError as e:
        print(f"\n[Error] ダウンロードエラー: URLが無効か、動画が非公開です。\n詳細: {e}")
    except Exception as e:
        print(f"\n[Error] 予期せぬエラー: {e}")


def main():
    if not check_ffmpeg_installed():
        print("[Error] FFmpegが見つかりません。インストールしてください。")
        sys.exit(1)

    print("=== YouTube Audio Downloader (Custom Path & Filename) ===")
    print(f"デフォルト保存先: {DEFAULT_OUTPUT_DIR}")
    
    try:
        # 1. URL入力
        url = input("YouTube URLを入力: ").strip()
        if not url:
            print("URLが入力されていません。終了します。")
            return

        # 2. ファイル名入力
        filename_input = input("保存ファイル名を入力 (未入力でタイトルを使用): ").strip()
        custom_name = filename_input if filename_input else None

        # 3. 実行
        download_audio_as_mp3(url, DEFAULT_OUTPUT_DIR, custom_name)
        
    except KeyboardInterrupt:
        print("\n\n処理を中断しました。")
        sys.exit(0)


if __name__ == "__main__":
    main()