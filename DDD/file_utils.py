"""DDD配下の複数スクリプトで共通のファイル名サニタイズ処理・プロジェクトルート解決。

batch_download_discord.py / extract_youtube_urls.py がそれぞれ個別に
ほぼ同一のロジックを実装していた（DRY違反）ため、ここに集約する。
"""
import os
import re
from pathlib import Path


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


def resolve_my_home_system_root(current_dir: Path, search_depth: int = 3) -> Path:
    """DDD配下の各スクリプトから、姉妹サブシステムMY_HOME_SYSTEMのルートディレクトリを
    解決する共通ヘルパー(品質: プロジェクトルート解決の3スクリプト不統一を解消)。

    以前はbatch_download_discord.py(MY_HOME_SYSTEM_ROOT環境変数 + "services"
    ディレクトリを上位探索するロバストな方式)と、newface_monitor.py /
    extract_youtube_urls.py(`current_dir.parent / "MY_HOME_SYSTEM"`という固定の
    兄弟ディレクトリ前提の単純な方式)という異なる2方式がそれぞれ個別に実装
    されていた(DRY違反かつ挙動の不統一。前者は探索に失敗するとCURRENT_DIR自身に
    フォールバックしていたため、実際には`run_task.sh`が渡すPYTHONPATHに暗黙依存する
    形で動いていた)。

    解決順序:
        1. 環境変数 MY_HOME_SYSTEM_ROOT が設定されていればそれを最優先する。
        2. `current_dir.parent / "MY_HOME_SYSTEM"`(このリポジトリの標準レイアウト。
           実機デプロイ先の`develop/DDD`・`develop/MY_HOME_SYSTEM`双方の兄弟ディレクトリ
           構成にも合致する)に"services"ディレクトリが実在すればそれを使う。
        3. 上記が無ければ、current_dirから親ディレクトリを最大search_depth階層まで
           遡り、"services"サブディレクトリを持つ最初のディレクトリを使う。
        4. いずれも見つからなければ、開発者個人の環境に依存した固定パスへの
           フォールバックはせず、current_dir自身を返す(呼出し元はcore.*/services.*の
           importが失敗した場合のImportErrorハンドリングでフェイルソフトする設計のため、
           ここで例外を送出したり決め打ちのパスを返したりする必要はない)。

    Args:
        current_dir: 呼出し元スクリプトの`Path(__file__).resolve().parent`。
        search_depth: 手順3で遡る最大階層数。

    Returns:
        解決されたMY_HOME_SYSTEMルート(またはそれに準ずる)ディレクトリのPath。
    """
    env_root = os.getenv("MY_HOME_SYSTEM_ROOT")
    if env_root:
        return Path(env_root)

    sibling_root = current_dir.parent / "MY_HOME_SYSTEM"
    if (sibling_root / "services").exists():
        return sibling_root

    candidate = current_dir
    for _ in range(search_depth):
        if (candidate / "services").exists():
            return candidate
        candidate = candidate.parent

    return current_dir


class DiscordCircuitBreaker:
    """Discord Webhookへの連続送信失敗を検知し、それ以降の送信をスキップする
    プロセス内サーキットブレーカー。

    newface_monitor.py の DiscordNotifier.notify() には元々、Webhookが
    401/404を返した場合にその実行内の残り通知を打ち切る簡易的な仕組みが
    あったが、対象がHTTPステータスの一部(401/404)に限られており、
    タイムアウトや接続エラーなど他の失敗モードでは何度でも送信を再試行し
    続けていた。また batch_download_discord.py 側の DiscordNotifier.send()
    には同種の仕組みが一切無く、Webhookが機能していない間の1回の実行で
    無駄なリクエストを送り続けていた。本クラスは両スクリプトで共通利用
    できる「連続N回失敗したら以降はスキップする」ロジックを提供する。

    cron等で毎回新規プロセスとして起動される運用のため、プロセスをまたいだ
    状態は永続化しない(次回実行は必ず閉じた状態から始まる)。
    """

    def __init__(self, failure_threshold: int = 3):
        self._failure_threshold = failure_threshold
        self._consecutive_failures = 0
        self._open = False

    @property
    def is_open(self) -> bool:
        """送信をスキップすべき(連続失敗数が閾値に達した)場合True。"""
        return self._open

    def record_success(self) -> None:
        """送信成功時に呼び出し、連続失敗カウントとブレーカー状態をリセットする。"""
        self._consecutive_failures = 0
        self._open = False

    def record_failure(self) -> None:
        """送信失敗時に呼び出す。連続失敗数が閾値に達すると自動的にブレーカーを開く。"""
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._failure_threshold:
            self._open = True

    def trip(self) -> None:
        """Webhook自体が無効/失効している等、再試行が明らかに無意味と判明した
        場合に、閾値を待たず即座にブレーカーを開く。"""
        self._open = True
