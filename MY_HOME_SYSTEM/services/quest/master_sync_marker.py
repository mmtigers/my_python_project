"""マスタ定義ソースの鮮度マーカー(Issue #700)。

`quest_data.py` から退役させたクエストが実機の `quest_master` に残り続け、
退役前の報酬と移設先(「きょうのすごろく」のステップ報酬)の両方が有効になって
二重報酬が発生していた。原因は `GameSystem.sync_master_data()` がどのデプロイ経路
からも自動実行されないことで、`unified_server.py` の `lifespan` が起動時に行うのは
マイグレーション適用だけだった。

ここでは family-quest の `deploy.sh --if-stale`(ビルド元のgitツリーハッシュを
`dist/.built-tree` に記録し、一致すればビルドをスキップする冪等モード)と同じ
考え方を、マスタ定義ソースに対して実装する。前回同期したソースのダイジェストを
マーカーに記録し、**差分があるときだけ**同期を走らせることで、`DELETE ... NOT IN`
を含む破壊的操作の自動実行頻度を増やさずにドリフトを回収する。

判定材料に git のツリーハッシュではなく**ファイル内容の SHA-256** を使うのは:

- 実機では git 管理外の編集(`git reset --hard` 以外の経路、手元での緊急修正)も
  ありうる。`deploy.sh` の注記にあるとおりツリーハッシュは未コミットの変更を
  反映しないため、その状態だと同期漏れを検知できない。
- git が使えない状況(tarball 配置等)でも判定できる。

対象は `quest_data.py`(`quest_master`/`reward_master` の同期元)と
`routine_data.py`(退役クエストの報酬の移設先)の2ファイル。`routine_data.py`
自体はDBテーブルを持たない定数だが、クエストの退役は「`quest_data.py` から消して
`routine_data.py` のステップ報酬へ移す」という**2ファイルにまたがる1つの操作**で
行われるため、どちらが変わっても同期を促す側に倒す(同期は冪等で、差分が無ければ
UPSERT が同じ値を書くだけ)。
"""
from __future__ import annotations

import hashlib
import os

import config
from core import state_file
from core.logger import get_logger

logger = get_logger("master_sync_marker")

# ダイジェストの対象(config.BASE_DIR からの相対パス)。
MASTER_SOURCE_FILENAMES: tuple[str, ...] = ("quest_data.py", "routine_data.py")

# マーカーのファイル名。health_watch の各マーカー(.claude_watch_marker 等)と同じく
# config.LOG_DIR 配下へ置く。logs/ は gitignore 済みで、logrotate の対象は *.log の
# ため、このドットファイルはローテートされない。失われても「差分あり」と判定されて
# 同期が1回余分に走るだけで、実害は無い(同期は冪等)。
MARKER_BASENAME: str = ".quest_master_sync_marker"


def get_marker_path() -> str:
    """マーカーの絶対パスを返す。

    `config.LOG_DIR` は import 時ではなく参照時に解決される(#664 の遅延解決)ため、
    モジュールレベルの定数にはせず関数で都度組み立てる。
    """
    return os.path.join(config.LOG_DIR, MARKER_BASENAME)


def compute_master_digest(base_dir: str | None = None) -> str:
    """マスタ定義ソースの内容から決定論的なダイジェストを計算する。

    ファイル名・バイト長・内容の順に混ぜることで、ファイル間の境界が曖昧になって
    別々の内容が同じダイジェストになる(連結の衝突)ことを避ける。

    Raises:
        OSError: 対象ファイルが存在しない/読めない場合。呼び出し側は
            「判定不能」として同期をスキップし、マーカーも更新しないこと
            (次回の実行で再試行される)。
    """
    base = base_dir if base_dir is not None else config.BASE_DIR
    digest = hashlib.sha256()
    for name in MASTER_SOURCE_FILENAMES:
        with open(os.path.join(base, name), "rb") as f:
            payload = f.read()
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(payload)).encode("ascii"))
        digest.update(b"\0")
        digest.update(payload)
    return digest.hexdigest()


def read_recorded_digest(marker_path: str | None = None) -> str | None:
    """マーカーに記録されたダイジェストを読む。無い/壊れている場合は None。"""
    raw = state_file.read_text(marker_path or get_marker_path())
    return raw or None


def write_recorded_digest(digest: str, marker_path: str | None = None) -> bool:
    """同期に成功したダイジェストをマーカーへ原子的に記録する。

    書き込み失敗(権限等)は例外にせず False を返す。次回の実行が
    「差分あり」と判定して同期をやり直すだけで、安全側に倒れる。
    """
    return state_file.write_text_atomic(marker_path or get_marker_path(), digest)


def is_stale(
    base_dir: str | None = None, marker_path: str | None = None
) -> tuple[bool, str]:
    """マスタ定義ソースが前回同期時から変化しているかを返す。

    Returns:
        (差分があるか, 現在のダイジェスト)。マーカーが無い初回実行は常に
        「差分あり」になる(実機DBが同期済みかどうかを知る術が無いため、
        1回だけ余分に同期する側へ倒す)。
    """
    digest = compute_master_digest(base_dir)
    recorded = read_recorded_digest(marker_path)
    return recorded != digest, digest
