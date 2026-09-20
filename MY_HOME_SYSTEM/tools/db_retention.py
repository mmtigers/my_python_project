#!/usr/bin/env python3
# MY_HOME_SYSTEM/tools/db_retention.py
"""SQLite の行の保持期間削除を、実機で手元から確認・実行するための CLI (Issue #733)。

`services/db_retention_service.py` の仕組みは `nas_monitor` から1日1回自動で
呼ばれるが、**既定はドライラン**で1行も消さない。有効化してよいかを判断するには
「どのテーブルがどれだけ育っていて、有効化すると何行消えるのか」を実機で見る必要が
あるため、その確認と、必要なら手動実行・`VACUUM` までを担う。

使い方(実機の MY_HOME_SYSTEM/ で):

    .venv/bin/python tools/db_retention.py            # 現状と削除予定の確認(何も消さない)
    .venv/bin/python tools/db_retention.py --apply    # 保持期間を超えた行を実際に削除する
    .venv/bin/python tools/db_retention.py --vacuum   # DELETE 後にファイルを縮める

`--apply` は `config.DB_ROW_RETENTION_ENABLED` が False でも削除する
(このコマンドを打つこと自体が明示的な意思表示のため)。自動実行のほうを有効に
するには `.env` に `DB_ROW_RETENTION_ENABLED=true` を設定する。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from services import db_retention_service as retention


def _human_bytes(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024 or unit == "GB":
            return f"{num:.1f}{unit}"
        num /= 1024
    return f"{num:.1f}GB"


def print_report() -> None:
    """現在の行数と削除予定件数を表示する。何も削除しない。"""
    report = retention.build_report()

    print(f"DB: {report['db_path']}  ({_human_bytes(report['db_bytes'])})")
    print(f"VACUUM で回収可能: {_human_bytes(report['reclaimable_bytes'])}")
    print(f"自動削除(DB_ROW_RETENTION_ENABLED): {'有効' if report['enabled'] else '無効(ドライラン)'}")
    print()

    print("=== 削除対象に登録済みのテーブル ===")
    plans = report["plans"]
    if not plans:
        print("  (対象テーブルが DB に存在しません)")
    for plan in plans:
        print(
            f"  {plan.target.table:<28} 全{plan.total_rows:>9,}行  "
            f"削除予定{plan.deletable_rows:>9,}行  "
            f"保持{plan.retention_days}日(境界 {plan.cutoff})"
        )
        if plan.oldest:
            print(f"  {'':<28} 最古 {plan.oldest} / 最新 {plan.newest}")
    print(f"  合計 削除予定: {sum(p.deletable_rows for p in plans):,}行")
    print()

    # 登録していないテーブルも行数を出す。どれを対象に追加すべきかを
    # 実測で判断するための材料(#733 の保留理由がまさにこれだった)。
    print("=== 未登録のテーブル(削除されません。行数の多い順) ===")
    unmanaged = sorted(
        (t for t in report["tables"] if not t["managed"]),
        key=lambda t: t["rows"], reverse=True,
    )
    for t in unmanaged[:20]:
        print(f"  {t['table']:<28} {t['rows']:>9,}行")


def do_apply() -> int:
    """保持期間を超えた行を実際に削除する。"""
    ok, detail = retention.recent_backup()
    if not ok:
        print(f"❌ 中止: 直近のバックアップを確認できません（{detail}）", file=sys.stderr)
        print("   行削除は不可逆です。先に services/backup_service.py を実行してください。", file=sys.stderr)
        return 1

    plans = retention.plan_deletions()
    total = sum(p.deletable_rows for p in plans)
    if total == 0:
        print("削除対象の行はありません。")
        return 0

    print(f"バックアップ確認 OK: {detail}")
    print(f"{total:,} 行を削除します...")
    started = time.time()
    results = retention.apply_deletions(plans)
    elapsed = time.time() - started

    for result in results:
        if result.deleted_rows:
            suffix = "（1回あたりの上限に到達。残りは次回）" if result.capped else ""
            print(f"  {result.target.table:<28} {result.deleted_rows:>9,}行 削除{suffix}")
    print(f"完了: {sum(r.deleted_rows for r in results):,}行 / {elapsed:.1f}秒")
    print(f"VACUUM で回収可能: {_human_bytes(retention.reclaimable_bytes())}（--vacuum で実行）")
    return 0


def do_vacuum() -> int:
    """`VACUUM` を実行してファイルを縮める。

    自動実行させず CLI に分けているのは、`VACUUM` が DB 全体を書き直す操作で、
    (1) 実行中は排他ロックが掛かりサーバーの書き込みが止まる、
    (2) 一時的に DB と同じサイズの空き容量を要求する、の2点が実機の
    ファイルサイズとディスク残量に依存するため。空き容量は事前に確認する。
    """
    db_path = config.SQLITE_DB_PATH
    if not os.path.exists(db_path):
        print(f"❌ DB が見つかりません: {db_path}", file=sys.stderr)
        return 1

    db_size = os.path.getsize(db_path)
    free = shutil.disk_usage(os.path.dirname(os.path.abspath(db_path))).free
    print(f"DB サイズ: {_human_bytes(db_size)} / ディスク空き: {_human_bytes(free)}")
    if free < db_size * 2:
        print(
            "❌ 中止: VACUUM は一時的に DB と同程度の空き容量を必要とします"
            "（安全側に倒して2倍を要求しています）。",
            file=sys.stderr,
        )
        return 1

    print("VACUUM 実行中（この間サーバーの書き込みはブロックされます）...")
    started = time.time()
    try:
        # VACUUM はトランザクション内で実行できないため、
        # core.database の get_db_cursor(commit=True) ではなく直接接続する。
        conn = sqlite3.connect(db_path, timeout=300.0, isolation_level=None)
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()
    except sqlite3.Error as e:
        print(f"❌ VACUUM に失敗しました: {e}", file=sys.stderr)
        return 1

    after = os.path.getsize(db_path)
    print(
        f"完了: {_human_bytes(db_size)} → {_human_bytes(after)} "
        f"({_human_bytes(db_size - after)} 削減 / {time.time() - started:.1f}秒)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SQLite の行の保持期間削除 (Issue #733)")
    parser.add_argument("--apply", action="store_true", help="保持期間を超えた行を実際に削除する")
    parser.add_argument("--vacuum", action="store_true", help="VACUUM でファイルを縮める")
    args = parser.parse_args(argv)

    if not args.apply and not args.vacuum:
        print_report()
        return 0

    code = 0
    if args.apply:
        code = do_apply()
        if code != 0:
            return code
    if args.vacuum:
        code = do_vacuum()
    return code


if __name__ == "__main__":
    sys.exit(main())
