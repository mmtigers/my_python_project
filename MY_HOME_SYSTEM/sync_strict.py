"""quest_data.py の内容で quest_master/reward_master を完全同期する手動実行CLI。

Issue #664: 同期の実体(`DELETE ... NOT IN` + UPSERT)は
`services/quest/game_system.py` の `GameSystem.sync_master_data(strict=True)` へ統合した。
以前はここと `GameSystem.sync_master_data()` に「マスタ→DB同期」が二重に書かれており、
UPSERT の列リストが食い違う事故が #100(`reset_period` 欠落)・#164(時間帯/期間/出現率/
前提クエスト欠落)・#165(`description` 欠落)と3度起きていた。#664 の前段で SQL 自体は
`services/quest/master_sync_sql.py` へ寄せてあったが、削除方針とUPSERTのループは
残っていたため、ここで完全に1本化する。

本ファイルに残るのはCLIの責務だけである:

- 引数解析(`--dry-run` / `--yes` / `--allow-empty-master`)
- 破壊的操作に対する安全ガード(M-9-6): マスタが空のままの実行を拒否し、
  非dry-run時は対話的な確認プロンプトを出す

`strict=True` が API 経路(`POST /api/quest/seed` = `strict=False`)と違う点は
`GameSystem.sync_master_data` のdocstringにまとめてある。
"""
import argparse
import sys

from core.logger import setup_logging
from services.quest.game_system import game_system, load_master_module

# ロガー設定
logger = setup_logging("strict_sync")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "quest_data.py の内容でDBを完全同期する。quest_data.py に無いID の行は "
            "quest_master/reward_master からDELETEされる破壊的操作であるため、実行前に "
            "確認プロンプトを表示する。"
        )
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="実際にはDBを変更せず、削除・更新される件数のみ表示する。"
    )
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help="確認プロンプトをスキップして実行する(自動実行向け)。"
    )
    parser.add_argument(
        "--allow-empty-master", action="store_true",
        help=(
            "quest_data.QUESTS または REWARDS が空でも実行を許可する。"
            "指定しない場合、空リストは quest_data.py のインポートミス等による"
            "意図しない全件削除の可能性が高いとみなして拒否する。"
        )
    )
    return parser


class SyncAborted(Exception):
    """M-9-6: ユーザーがマスタ同期の確認プロンプトで拒否した、または安全ガードで拒否された場合。"""


def confirm_or_abort(
    master_quest_ids: list, master_reward_ids: list,
    allow_empty_master: bool, assume_yes: bool,
    input_func=input,
) -> None:
    """
    M-9-6: sync_strict.py はマスタに無い行を無確認でDELETEする(マスタが空なら全削除)。
    quest_data.py のID変更ミス一発で本番マスタが消えるリスクがあるため、実行前に
    安全ガード(空マスタの拒否)と対話的な確認プロンプトを挟む。

    Raises:
        SyncAborted: 安全ガードまたはユーザーの拒否により実行を中止すべき場合。
    """
    if (not master_quest_ids or not master_reward_ids) and not allow_empty_master:
        logger.error(
            "❌ quest_data.QUESTS または REWARDS が空です。このまま実行すると "
            "quest_master/reward_master が全件削除されます。意図的な場合は "
            "--allow-empty-master を指定してください。"
        )
        raise SyncAborted("empty master data without --allow-empty-master")

    if assume_yes:
        return

    answer = input_func(
        "この操作はquest_data.pyに存在しないデータをDBから削除します。続行しますか？ [y/N]: "
    )
    if answer.strip().lower() not in ("y", "yes"):
        logger.info("Sync aborted by user.")
        raise SyncAborted("user declined confirmation prompt")


def run_sync(dry_run: bool = False, assume_yes: bool = False, allow_empty_master: bool = False, input_func=input) -> None:
    logger.info("Starting Strict Master Data Sync (v3.0)...")

    if not dry_run:
        # 安全ガードは「これから同期されるマスタ」を数える必要があるため、
        # sync_master_data と同じ読み込み口(load_master_module)を使う。
        # 別々に読むと、ガードが見たマスタと実際に書かれるマスタがずれうる。
        quest_data = load_master_module()
        confirm_or_abort(
            [q['id'] for q in quest_data.QUESTS],
            [r['id'] for r in quest_data.REWARDS],
            allow_empty_master, assume_yes, input_func=input_func,
        )

    game_system.sync_master_data(strict=True, dry_run=dry_run)


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    try:
        run_sync(
            dry_run=args.dry_run,
            assume_yes=args.yes,
            allow_empty_master=args.allow_empty_master,
        )
    except SyncAborted:
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Sync failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
