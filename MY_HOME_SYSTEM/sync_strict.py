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

        # #100: reset_period 列を明示的にINSERTしないと、quest_master.reset_period の
        # DB列デフォルト('weekly_monday'。current_schema.sql/migrations/0002で焼き付いており
        # ALTER TABLEでは変更不能)がそのまま入ってしまう。'weekly_monday' は
        # is_within_reset_period() が扱えない値のため、周期内多重完了ガードが機能せず、
        # クリアしても未クリア表示になる不具合(0005で一度修正済み)が新規/再UPSERT行で
        # 再発する。quest_data.py の各クエストは reset_period キーを持たないため、
        # models.quest.MasterQuest.reset_period のデフォルトと同じ 'daily' を使う。
        reset_period_val = q.get('reset_period', 'daily')

        # #164: 時間帯(start_time/end_time)・期間(start_date/end_date)・出現率
        # (occurrence_chance)・前提クエスト(pre_requisite_quest_id)も
        # sync_master_data()(services/quest_service.py)と同じ完全同期対象とする。
        # これらを列リストから欠落させると、時間帯限定クエストが再UPSERT時に
        # NULL(=filter_active_quests()で終日扱い)に上書きされてしまう。
        # models.quest.MasterQuest のデフォルトと合わせ、occurrence_chanceのみ
        # 未指定時のデフォルトを1.0とする。
        cur.execute("""
            INSERT INTO quest_master (
                quest_id, title, quest_type, target_user,
                exp_gain, gold_gain, icon_key,
                day_of_week, description, reset_period,
                start_time, end_time, start_date, end_date,
                occurrence_chance, pre_requisite_quest_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(quest_id) DO UPDATE SET
                title = excluded.title,
                quest_type = excluded.quest_type,
                target_user = excluded.target_user,
                exp_gain = excluded.exp_gain,
                gold_gain = excluded.gold_gain,
                icon_key = excluded.icon_key,
                day_of_week = excluded.day_of_week,
                description = excluded.description,
                reset_period = excluded.reset_period,
                start_time = excluded.start_time,
                end_time = excluded.end_time,
                start_date = excluded.start_date,
                end_date = excluded.end_date,
                occurrence_chance = excluded.occurrence_chance,
                pre_requisite_quest_id = excluded.pre_requisite_quest_id
        """, (
            q['id'],
            q['title'],
            q.get('type', 'daily'),     # type
            q.get('target', 'all'),     # target
            exp_val,
            gold_val,
            icon_val,
            q.get('days'),              # days (0,1,2...)
            q.get('desc'),              # desc -> description
            reset_period_val,
            q.get('start_time'),
            q.get('end_time'),
            q.get('start_date'),
            q.get('end_date'),
            q.get('chance', 1.0),       # chance -> occurrence_chance
            q.get('pre_requisite_quest_id'),
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

    # 削除対象の抽出(削除自体は下のFKチェック付きループで行う)
    if master_ids:
        placeholders = ','.join(['?'] * len(master_ids))
        stale_rewards = cur.execute(
            f"SELECT reward_id FROM reward_master WHERE reward_id NOT IN ({placeholders})", master_ids
        ).fetchall()
    else:
        stale_rewards = cur.execute("SELECT reward_id FROM reward_master").fetchall()
        logger.info("Master is empty: all rewards are candidates for deletion")

    # #165: user_inventory は reward_master(reward_id) へのFK(PRAGMA foreign_keys=ON、
    # core/database.py:24)を持つため、所持者がいる(所有中/申請中/使用済問わず
    # user_inventoryに行が残る)報酬を無条件でDELETEするとIntegrityErrorとなり、
    # run_sync全体がexit 1する。services/quest_service.pyのsync_master_data()側では
    # M-1-2としてこの対策済み(参照が残っている報酬は削除をスキップし警告ログのみ出す)
    # だが、sync_strict.py側には未展開だった。同じ対策をここにも適用する。
    for row in stale_rewards:
        stale_reward_id = row['reward_id']
        still_referenced = cur.execute(
            "SELECT 1 FROM user_inventory WHERE reward_id = ? LIMIT 1", (stale_reward_id,)
        ).fetchone()
        if still_referenced:
            logger.warning(
                f"⚠️ reward_id={stale_reward_id} はマスタから削除されましたが、"
                "user_inventoryに参照が残っているため削除をスキップします。"
            )
            continue
        cur.execute("DELETE FROM reward_master WHERE reward_id = ?", (stale_reward_id,))

    # Upsert
    for r in REWARDS:
        cost_val = r.get('cost_gold', r.get('cost', 0))
        icon_val = r.get('icon_key', r.get('icon', '🎁'))

        # ★修正: target と desc を同期対象に追加
        target_val = r.get('target', 'all')
        desc_val = r.get('desc', '')

        # #165: 従来はレガシー列の desc のみ書き込んでおり、アプリが実際に読む
        # description 列(InventoryService.get_user_inventoryの`rm.description as desc`。
        # services/quest_service.py:848)が更新されないままだった。sync_strict経由で
        # 登録・更新された報酬は所持済みアイテム一覧で説明が空表示になり、
        # sync_master_data(descriptionへ書く)との実行順で表示が食い違っていた。
        # 両列を同じ値で同期する。
        cur.execute("""
            INSERT INTO reward_master (
                reward_id, title, category, cost_gold, icon_key, target, desc, description
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(reward_id) DO UPDATE SET
                title = excluded.title,
                category = excluded.category,
                cost_gold = excluded.cost_gold,
                icon_key = excluded.icon_key,
                target = excluded.target,
                desc = excluded.desc,
                description = excluded.description
        """, (
            r['id'],
            r['title'],
            r.get('category', 'small'),
            cost_val,
            icon_val,
            target_val,
            desc_val,
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
