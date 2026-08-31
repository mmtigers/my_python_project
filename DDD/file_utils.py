"""DDD配下の複数スクリプトで共通のファイル名サニタイズ処理。

batch_download_discord.py / extract_youtube_urls.py がそれぞれ個別に
ほぼ同一のロジックを実装していた（DRY違反）ため、ここに集約する。
"""
import re


def sanitize_filename(filename: str, max_length: int = 200) -> str:
    """ファイル名として使用できない文字を置換し、長さを制限する。

    Args:
        filename: 元の文字列。
        max_length: 生成するファイル名の最大バイト数（UTF-8エンコード後、拡張子は
            含まない前提）。ext4等の255バイト制限に対する安全マージンとして
            既定200バイト。

    Returns:
        安全なファイル名文字列。
    """
    safe = re.sub(r'[\\/*?:"<>|]', '_', filename).strip()

    # #175: 以前は safe[:max_length] で「文字数」を制限していたが、UTF-8では
    # 日本語1文字が3バイトになるため、200文字(最大600バイト)がext4等の255バイト
    # 上限を容易に超過しファイル操作がENAMETOOLONGで失敗しうる不具合があった。
    # バイト単位でエンコードしてから切り詰め、マルチバイト文字の境界で
    # 分断された末尾の不完全なバイト列は errors='ignore' で安全に除去する。
    encoded = safe.encode('utf-8')
    if len(encoded) > max_length:
        safe = encoded[:max_length].decode('utf-8', errors='ignore')

    safe = safe.strip('. ')
    if not safe:
        # Low: 入力が ".." や "." 等の記号のみで構成されている場合、ここまでの
        # 処理で空文字列になりうる。呼び出し側は戻り値へ拡張子を連結するだけの
        # ものが多く(例: sanitize_filename(video_id) + ".mp4")、空文字のままだと
        # ".mp4" のような隠しファイル(空stem)が生成されてしまうため、安全な
        # フォールバック名を補う。
        safe = "untitled"
    return safe
