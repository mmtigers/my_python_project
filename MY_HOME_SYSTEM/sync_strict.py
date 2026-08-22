import argparse
import sys
import common  # プロジェクト共通モジュール
from quest_data import QUESTS, REWARDS, USERS  # マスターデータ

# ロガー設定
logger = common.setup_logging("strict_sync")


def _count_rows_to_delete(cur, table: str, id_column: str, master_ids: list) -> int:
    """quest_master/reward_master のうち、マスタに存在しないため削除対象になる行数を数える。"""
    if master_ids:
        placeholders = ','.join(['?'] * len(master_ids))
        row = cur.execute(
            f"SELECT COUNT(*) as c FROM {table} WHERE {id_column} NOT IN ({placeholders})", master_ids
        ).fetchone()
    else:
        row = cur.execute(f"SELECT COUNT(*) as c FROM {table}").fetchone()
    return row['c'] if row else 0


def sync_quests(cur, dry_run: bool = False):
    """クエスト定義の完全同期 (不要なデータは削除)"""
    logger.info("--- Syncing Quests (Strict Mode) ---")

    # 1. マスターデータ内のIDリストを取得
    master_ids = [q['id'] for q in QUESTS]

    if dry_run:
        stale_count = _count_rows_to_delete(cur, "quest_master", "quest_id", master_ids)
        logger.info(f"[dry-run] Would delete obsolete quests: {stale_count} rows")
        logger.info(f"[dry-run] Would upsert {len(QUESTS)} quests.")
        return

    # 2. マスターに存在しない古いデータをDBから削除 (Clean Up)
    if master_ids:
        placeholders = ','.join(['?'] * len(master_ids))
        sql_delete = f"DELETE FROM quest_master WHERE quest_id NOT IN ({placeholders})"
        cur.execute(sql_delete, master_ids)
        logger.info(f"Deleted obsolete quests: {cur.rowcount} rows")
    else:
        cur.execute("DELETE FROM quest_master")
        logger.info("Deleted ALL quests (Master is empty)")

    # 3. マスターデータをUpsert
    for q in QUESTS:
        exp_val = q.get('exp_gain', q.get('exp', 0))
        gold_val = q.get('gold_gain', q.get('gold', 0))
        icon_val = q.get('icon_key', q.get('icon', '📝'))

        # ★修正: days, type, target, desc などの主要カラムも同期するように拡張
        # (init_unified_db.py の定義と一致させる)
        # M-9-6: quest_master の実カラム名は day_of_week であり、存在しない
        # `days` カラムを参照していたため実行すると必ず sqlite3.OperationalError
        # になっていた(テスト作成時に発覚)。

        cur.execute("""
            INSERT INTO quest_master (
                quest_id, title, quest_type, target_user,
                exp_gain, gold_gain, icon_key,
                day_of_week, description
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(quest_id) DO UPDATE SET
                title = excluded.title,
                quest_type = excluded.quest_type,
                target_user = excluded.target_user,
                exp_gain = excluded.exp_gain,
                gold_gain = excluded.gold_gain,
                icon_key = excluded.icon_key,
                day_of_week = excluded.day_of_week,
                description = excluded.description
        """, (
            q['id'],
            q['title'],
            q.get('type', 'daily'),     # type
            q.get('target', 'all'),     # target
            exp_val,
            gold_val,
            icon_val,
            q.get('days'),              # days (0,1,2...)
            q.get('desc')               # desc -> description
        ))
    logger.info(f"Upserted {len(QUESTS)} quests.")

def sync_rewards(cur, dry_run: bool = False):
    """報酬データの完全同期"""
    logger.info("--- Syncing Rewards ---")
    master_ids = [r['id'] for r in REWARDS]

    if dry_run:
        stale_count = _count_rows_to_delete(cur, "reward_master", "reward_id", master_ids)
        logger.info(f"[dry-run] Would delete obsolete rewards: {stale_count} rows")
        logger.info(f"[dry-run] Would upsert {len(REWARDS)} rewards.")
        return

    # 削除
    if master_ids:
        placeholders = ','.join(['?'] * len(master_ids))
        cur.execute(f"DELETE FROM reward_master WHERE reward_id NOT IN ({placeholders})", master_ids)
    else:
        cur.execute("DELETE FROM reward_master")
        logger.info("Deleted ALL rewards (Master is empty)")

    # Upsert
    for r in REWARDS:
        cost_val = r.get('cost_gold', r.get('cost', 0))
        icon_val = r.get('icon_key', r.get('icon', '🎁'))

        # ★修正: target と desc を同期対象に追加
        target_val = r.get('target', 'all')
        desc_val = r.get('desc', '')

        cur.execute("""
            INSERT INTO reward_master (
                reward_id, title, category, cost_gold, icon_key, target, desc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(reward_id) DO UPDATE SET
                title = excluded.title,
                category = excluded.category,
                cost_gold = excluded.cost_gold,
                icon_key = excluded.icon_key,
                target = excluded.target,
                desc = excluded.desc
        """, (
            r['id'],
            r['title'],
            r.get('category', 'small'),
            cost_val,
            icon_val,
            target_val,
            desc_val
        ))
    logger.info(f"Upserted {len(REWARDS)} rewards.")


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
    logger.info("Starting Strict Master Data Sync (v2.1)...")

    master_quest_ids = [q['id'] for q in QUESTS]
    master_reward_ids = [r['id'] for r in REWARDS]

    if not dry_run:
        confirm_or_abort(master_quest_ids, master_reward_ids, allow_empty_master, assume_yes, input_func=input_func)

    with common.get_db_cursor(commit=not dry_run) as cur:
        sync_quests(cur, dry_run=dry_run)
        sync_rewards(cur, dry_run=dry_run)

    if dry_run:
        logger.info("✅ Dry-run completed. No changes were made.")
    else:
        logger.info("✅ Sync completed successfully.")


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
