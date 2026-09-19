"""quest_data.py の内容で quest_master/reward_master を完全同期する手動実行CLI。

Issue #664: 同期の実体(`DELETE ... NOT IN` + UPSERT)は
`services/quest/game_system.py` の `GameSystem.sync_master_data(strict=True)` へ統合した。
以前はここと `GameSystem.sync_master_data()` に「マスタ→DB同期」が二重に書かれており、
UPSERT の列リストが食い違う事故が #100(`reset_period` 欠落)・#164(時間帯/期間/出現率/
前提クエスト欠落)・#165(`description` 欠落)と3度起きていた。#664 の前段で SQL 自体は
`services/quest/master_sync_sql.py` へ寄せてあったが、削除方針とUPSERTのループは
残っていたため、ここで完全に1本化する。

本ファイルに残るのはCLIの責務だけである:

- 引数解析(`--dry-run` / `--yes` / `--allow-empty-master` / `--if-stale`)
- 破壊的操作に対する安全ガード(M-9-6): マスタが空のままの実行を拒否し、
  非dry-run時は対話的な確認プロンプトを出す

`strict=True` が API 経路(`POST /api/quest/seed` = `strict=False`)と違う点は
`GameSystem.sync_master_data` のdocstringにまとめてある。

Issue #700: デプロイ経路(`deploy/git-hooks/post-merge` と
`start_all.sh` の前処理フェーズ)から無人で呼ぶための `--if-stale` を追加した。
マスタ定義ソース(`quest_data.py`/`routine_data.py`)のダイジェストを
`services/quest/master_sync_marker.py` のマーカーと突き合わせ、**差分がある
ときだけ**同期する(family-quest の `deploy.sh --if-stale` と同じ冪等モード)。
このモードだけは `strict=False` で同期する — 無人実行では確認プロンプトを
出せないため、マスタが空になった場合に `quest_master` を全削除する
`strict=True` の方針は危険すぎる。詳細は `run_sync_if_stale` を参照。
"""
import argparse
import sys

from core.logger import setup_logging
from services.quest import master_sync_marker
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
    parser.add_argument(
        "--if-stale", action="store_true", dest="if_stale",
        help=(
            "quest_data.py / routine_data.py が前回同期時から変化している場合だけ同期する"
            "(冪等モード。デプロイ経路からの無人実行向けで、確認プロンプトは出さず "
            "strict=False で同期する)。"
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


def run_sync(
    dry_run: bool = False, assume_yes: bool = False, allow_empty_master: bool = False,
    input_func=input, strict: bool = True,
) -> None:
    """同期を1回実行する。既定(`strict=True`)は手動CLIの従来どおりの挙動。

    Args:
        strict: `GameSystem.sync_master_data` へそのまま渡す。`--if-stale`
            (無人実行)だけが `False` を渡し、マスタが空になった場合に
            `quest_master` を全削除しない #242 の安全弁を効かせる(Issue #700)。
    """
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

    game_system.sync_master_data(strict=strict, dry_run=dry_run)


def run_sync_if_stale(dry_run: bool = False, base_dir=None, marker_path=None) -> int:
    """マスタ定義ソースに差分があるときだけ同期する冪等モード(Issue #700)。

    デプロイ経路(`deploy/git-hooks/post-merge` / `start_all.sh`)から無人で
    呼ばれる。差分が無ければDBには一切触れない(接続すら開かない)ため、
    `DELETE ... NOT IN` を含む破壊的操作の実行頻度は「マスタ定義を編集した
    デプロイの回数」を超えない。

    無人実行のための方針:

    - `strict=False` で同期する。`strict=True` はマスタが空のとき
      `quest_master` を全削除するが、無人実行では対話的な確認プロンプトで
      止められないため、#242 の安全弁(空マスタなら削除をスキップ)が効く
      `strict=False` を使う。差分のある通常ケースの挙動(マスタに無い行の
      DELETE + UPSERT)は strict と同じで、今回の退役クエスト残留は解消される。
    - それでも `confirm_or_abort` の空マスタガードは `assume_yes=True` /
      `allow_empty_master=False` で通す。`reward_master` 側は strict でなくても
      「マスタが空なら参照の無い報酬を全削除」する経路が残っているため、
      quest_data.py のimportミス等で空になった場合はここで止める。
    - 同期が失敗・中止した場合はマーカーを更新しない(次回の実行で再試行する)。

    同期の前に `init_unified_db.init_db()`(= マイグレーションの適用と検証だけを行う
    薄いラッパー)を通す。このモードは `unified_server.py` の `lifespan` **より前**に
    走る(post-merge フック、および `start_all.sh` の前処理フェーズ)ため、新しい
    マイグレーションとマスタ定義の変更を同じ pull で受け取った場合、スキーマが
    未適用のまま UPSERT して失敗しうる。`init_db()` は適用済みなら何もしない。

    Returns:
        プロセスの終了コード相当。0 なら「最新のため何もしなかった」か
        「同期に成功した」。1 は判定不能または同期の失敗・中止。
    """
    try:
        stale, digest = master_sync_marker.is_stale(base_dir=base_dir, marker_path=marker_path)
    except OSError as e:
        # マスタ定義ソースが読めない = 判定不能。同期もマーカー更新もしない。
        logger.error(f"❌ マスタ定義ソースを読み取れないため同期可否を判定できません: {e}")
        return 1

    if not stale:
        logger.info(f"マスタ定義は同期済みです (digest {digest[:12]})。同期をスキップします。")
        return 0

    logger.info(f"マスタ定義に差分があります (digest {digest[:12]})。同期を実行します...")
    try:
        if not dry_run:
            # このモード専用の依存のため、モジュール先頭ではなくここでimportする
            # (手動CLI経路は従来どおりDB初期化に関与しない)。
            import init_unified_db

            init_unified_db.init_db()
        run_sync(dry_run=dry_run, assume_yes=True, allow_empty_master=False, strict=False)
    except SyncAborted:
        logger.error("❌ 安全ガードにより同期を中止しました。マーカーは更新しません。")
        return 1
    except Exception as e:  # noqa: BLE001 — 無人実行なので、失敗理由に関わらず
        # 「マーカーを進めずに終了コードで知らせる」に集約する(tracebackで
        # post-merge / start_all.sh のログを汚さず、次回の実行で再試行される)。
        logger.error(f"❌ Sync failed: {e}")
        return 1

    if dry_run:
        # dry-run は何も変更していないので、マーカーを進めると次回の同期が飛ぶ。
        logger.info("[dry-run] マーカーは更新しません。")
        return 0

    if not master_sync_marker.write_recorded_digest(digest, marker_path=marker_path):
        # 書けなくても同期自体は完了している。次回また同期が走るだけなので失敗扱いにしない。
        logger.warning("⚠️ 同期マーカーを更新できませんでした。次回の実行でも同期が走ります。")
    return 0


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    if args.if_stale:
        sys.exit(run_sync_if_stale(dry_run=args.dry_run))
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
