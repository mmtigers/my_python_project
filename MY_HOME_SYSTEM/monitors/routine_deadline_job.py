# MY_HOME_SYSTEM/monitors/routine_deadline_job.py
"""デイリールーティンの締切処理を定期的に起動するスケジューラタスク(Issue #738 / AUDIT-008)。

以前はチェックポイント(自由時間の終了予定時刻)超過による強制遷移とボーナス付与を
`GET /api/routine/today` が行っており、全端末が15秒間隔で叩くポーリングが
そのまま書き込みトランザクション・報酬付与のトリガーになっていた。加えて
「誰も画面を開いていなければ締切処理が走らない」という不整合もあった。

本スクリプトは `scheduler_boot.TASKS` に60秒間隔で登録され、サーバーの
`POST /api/routine/deadlines/process` を叩くだけの薄いクライアントである。

**DBを直接書き換えないこと。** スケジューラは unified_server とは別プロセスで動き、
`quest_users`(gold/exp/level)の排他は `services/quest/locks.py` の `threading.Lock`
＝サーバープロセス内の直列化だけで成立している(CLAUDE.md「並行制御は単一プロセス
前提」)。別プロセスから直接書くとロストアップデートが**エラー無しで**起きるため、
`reset_game.py`(#547)と同じくHTTP API を経由してサーバー側のロックに参加する。
"""
import os
import sys

import requests

# プロジェクトルートへのパス解決
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

import config
from core.logger import setup_logging

logger = setup_logging("monitor.routine_deadline")

API_PATH = "/api/routine/deadlines/process"


def run_once() -> int:
    """締切処理APIを1回呼ぶ。終了コード(0=正常)を返す。

    サーバーへ接続できない場合は WARNING に留めて 0 を返す。本タスクは60秒ごとに
    走るため、デプロイ時の再起動・起動待ちのような一時的な接続断で
    「タスク失敗」のDiscord通知を毎分出しても意味がなく、サーバーの死活そのものは
    `monitors/server_watchdog.py` と `monitors/health_watch.py` が別途監視している。
    サーバーが応答したうえでエラーを返した場合(＝サーバー側の不具合)だけ
    ERROR とし、スケジューラのタスク失敗として扱う。
    """
    url = f"{config.ROUTINE_DEADLINE_API_BASE_URL}{API_PATH}"
    try:
        res = requests.post(url, timeout=config.ROUTINE_DEADLINE_API_TIMEOUT_SEC)
    except requests.exceptions.RequestException as e:
        logger.warning(f"ルーティン締切処理APIへ接続できませんでした({url}): {e}")
        return 0

    if res.status_code != 200:
        logger.error(
            f"ルーティン締切処理APIがエラーを返しました: status={res.status_code} body={res.text[:500]}"
        )
        return 1

    try:
        payload = res.json()
    except ValueError:
        logger.error(f"ルーティン締切処理APIの応答がJSONではありません: {res.text[:500]}")
        return 1

    # 何も起きなかった実行(大多数)はdebugに落とし、ログを埋めない。
    if payload.get("transitions") or payload.get("failed_users"):
        logger.info(f"ルーティン締切処理: {payload}")
    else:
        logger.debug(f"ルーティン締切処理(変化なし): {payload}")
    return 0


def main() -> int:
    try:
        return run_once()
    except Exception:
        # 想定外の例外でもスタックトレースを残して終了コードで知らせる
        # (スケジューラ本体は run_script が返り値を見るだけなので巻き込まない)。
        logger.exception("ルーティン締切処理タスクで予期しないエラーが発生しました")
        return 1


if __name__ == "__main__":
    sys.exit(main())
