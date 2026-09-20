# MY_HOME_SYSTEM/services/db_retention_service.py
"""SQLite の「行」の保持期間削除 (Issue #733 / AUDIT-003)。

## 背景

NAS 上のファイルには保持期間削除があるのに、SQLite の行を削除・集約する
コードはリポジトリ内に1つも存在しなかった。`nas_monitor.run_retention_cleanup`
が消しているのは NVR 録画・スナップショット・DB バックアップ・HLS キャッシュで、
いずれも**ファイル**である。

SQLite は行を `DELETE` してもファイルが縮まない(解放されたページが freelist に
入るだけ)ため、保持期間削除を「後から足す」ほど `VACUUM` の所要時間と必要な
空き容量が増える。ラズパイの SD カード上の単一ファイルにすべてが載っている構成で、
これは時間とともに一方的に悪化する。

## 設計

**既定はドライラン。** `config.DB_ROW_RETENTION_ENABLED` が False のあいだ、
本モジュールは1行も削除せず「何がどれだけ消えるか」を報告するだけで終わる。
行削除は不可逆であり、テーブルごとの適切な保持期間は実機のデータ量を見ないと
決められないため、「仕組みを先に入れ、値は実測してから決める」順序を取っている。

削除対象は**明示的に登録したテーブルだけ**にしてある(`RETENTION_TARGETS`)。
一方 `build_report()` は DB 内の全テーブルの行数・最古/最新を返すので、
「対象に入れていないテーブルがどれだけ育っているか」も同じ出力で分かる。
これは「何をどれだけ消すか」を決めるための材料であって、それ自体は何も消さない。

### 対象に入れていないもの

- `quest_history` / `reward_history` — Family Quest の**年代記の原資**。単純に
  消すと家族の記録が失われる(#762 が同じ懸念を扱っている)。#733 の「推奨修正」
  どおり、削除ではなく日次サマリへの集約を別途設計すべき対象。
- `daily_logs` および `*_records` 系(食事・排便・子どもの体調・車・散髪等) —
  人が読む記録であり、センサーの生ログとはライフサイクルが違う。
- `weather_history` — `UNIQUE(date, location)` で1日1行/地点しか増えないため、
  そもそも蓄積の問題になっていない。
- `land_price_records` / `suumo_records` — 更新頻度が低く、蓄積の問題になっていない。

いずれも `build_report()` には現れるので、実測を見て後から登録できる。
"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import config
from core.database import get_db_cursor, get_ro_connection
from core.logger import setup_logging
from core.utils import get_now_jst

logger = setup_logging("db_retention")


@dataclass(frozen=True)
class RetentionTarget:
    """保持期間削除の対象テーブル1件。

    `retention_days_attr` を直接の日数ではなく `config` の属性名にしているのは、
    テストやローカル運用で `config` の値を差し替えたときに、モジュール import 時に
    焼き付いた古い値ではなく実行時の値が使われるようにするため
    (`config.SQLITE_DB_PATH` を monkeypatch する既存テストと同じ考え方)。
    """
    table: str
    timestamp_column: str
    retention_days_attr: str
    label: str


# 削除対象。ここに無いテーブルは1行も消えない。
RETENTION_TARGETS: tuple[RetentionTarget, ...] = (
    RetentionTarget("device_records", "timestamp", "DB_ROW_RETENTION_SENSOR_DAYS", "デバイス記録"),
    RetentionTarget("power_usage", "timestamp", "DB_ROW_RETENTION_SENSOR_DAYS", "電力ログ"),
    RetentionTarget("switchbot_meter_logs", "timestamp", "DB_ROW_RETENTION_SENSOR_DAYS", "メーターログ"),
    RetentionTarget("nas_records", "timestamp", "DB_ROW_RETENTION_SENSOR_DAYS", "NAS監視ログ"),
    RetentionTarget("bicycle_parking_records", "timestamp", "DB_ROW_RETENTION_SENSOR_DAYS", "駐輪場記録"),
    RetentionTarget("security_logs", "timestamp", "DB_ROW_RETENTION_SENSOR_DAYS", "防犯ログ"),
    RetentionTarget("routine_step_events", "occurred_at", "DB_ROW_RETENTION_EVENT_DAYS", "ルーティン遷移"),
)

# `build_report()` が行数を数える際、明らかに時系列でないテーブルは
# 最古/最新の算出をスキップするために使う(行数だけは数える)。
_SQLITE_INTERNAL_PREFIX = "sqlite_"


@dataclass
class TablePlan:
    """1テーブル分の「消える予定」。"""
    target: RetentionTarget
    retention_days: int
    cutoff: str
    total_rows: int
    deletable_rows: int
    oldest: str | None = None
    newest: str | None = None


@dataclass
class TableResult:
    """1テーブル分の実削除結果。"""
    target: RetentionTarget
    deleted_rows: int
    capped: bool = False


@dataclass
class RetentionOutcome:
    """1回の実行の結果。`nas_monitor` はこれを見て通知本文を組み立てる。"""
    dry_run: bool
    plans: list[TablePlan] = field(default_factory=list)
    results: list[TableResult] = field(default_factory=list)
    skipped_reason: str | None = None
    reclaimable_bytes: int = 0

    @property
    def planned_rows(self) -> int:
        return sum(p.deletable_rows for p in self.plans)

    @property
    def deleted_rows(self) -> int:
        return sum(r.deleted_rows for r in self.results)


def _retention_days(target: RetentionTarget) -> int:
    """対象の保持日数を実行時の config から引く。"""
    return int(getattr(config, target.retention_days_attr))


def _cutoff_string(retention_days: int, now=None) -> str:
    """保持期間の境界を DB に入っている文字列表現に合わせて作る。

    各テーブルの時刻列は `DATETIME`/`TEXT` で、書き込み側は
    `"%Y-%m-%d %H:%M:%S"` 形式の JST 文字列を入れている
    (`core/utils` の JST 固定ヘルパー経由)。SQLite の比較は辞書順になるため、
    同じ形式の文字列を作れば `<` がそのまま時刻の前後比較になる。
    """
    base = now or get_now_jst()
    cutoff_dt = base - timedelta(days=retention_days)
    return cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def plan_deletions(now=None) -> list[TablePlan]:
    """削除対象テーブルごとに「消える行数」を数える。**何も削除しない。**

    読み取り専用接続を使うので、この関数自体は書き込みロックを取らない。
    """
    plans: list[TablePlan] = []
    with get_ro_connection() as conn:
        for target in RETENTION_TARGETS:
            if not _table_exists(conn, target.table):
                # マイグレーションが未適用の古い DB でも落ちないようにする。
                logger.debug(f"retention: テーブルが存在しないためスキップ: {target.table}")
                continue

            retention_days = _retention_days(target)
            cutoff = _cutoff_string(retention_days, now)
            col = target.timestamp_column

            # テーブル名・列名は RETENTION_TARGETS のリテラル(外部入力ではない)で、
            # 値の cutoff は第2引数でパラメータ化している。bandit B608 はこの
            # 「識別子だけ f-string / 値はプレースホルダ」という安全なパターンを
            # 文字列連結によるSQLインジェクションと区別できず誤検知する
            # (services/quest/game_system.py と同じ抑制方針)。
            total = conn.execute(f"SELECT COUNT(*) FROM {target.table}").fetchone()[0]  # nosec B608
            deletable = conn.execute(
                f"SELECT COUNT(*) FROM {target.table} WHERE {col} < ?", (cutoff,)  # nosec B608
            ).fetchone()[0]
            bounds = conn.execute(
                f"SELECT MIN({col}), MAX({col}) FROM {target.table}"  # nosec B608
            ).fetchone()

            plans.append(
                TablePlan(
                    target=target,
                    retention_days=retention_days,
                    cutoff=cutoff,
                    total_rows=total,
                    deletable_rows=deletable,
                    oldest=bounds[0],
                    newest=bounds[1],
                )
            )
    return plans


def apply_deletions(
    plans: list[TablePlan],
    batch_size: int | None = None,
    max_rows_per_run: int | None = None,
) -> list[TableResult]:
    """`plans` に従って実際に行を削除する。

    1トランザクションあたり `batch_size` 行ずつ削るのは、SQLite の `DELETE` が
    その間ずっと書き込みロックを保持するため。一度に数十万行を消すと、
    同時に走っている Webhook の保存が `database is locked` のリトライ上限を
    超えてセンサーイベントを落としうる(#733 が指摘した問題の連鎖そのもの)。

    `max_rows_per_run` は1回の実行全体の上限。初回の積み残しが大きいときに
    1晩で全部消さず複数日に分けて削るための保険で、翌日の実行が続きを削る。
    """
    batch = batch_size if batch_size is not None else int(config.DB_ROW_RETENTION_BATCH_SIZE)
    remaining = (
        max_rows_per_run if max_rows_per_run is not None
        else int(config.DB_ROW_RETENTION_MAX_ROWS_PER_RUN)
    )

    results: list[TableResult] = []
    for plan in plans:
        if plan.deletable_rows <= 0:
            continue

        target = plan.target
        col = target.timestamp_column
        deleted = 0
        capped = False

        while deleted < plan.deletable_rows:
            if remaining <= 0:
                capped = True
                break
            chunk = min(batch, remaining, plan.deletable_rows - deleted)
            # 主キー経由のサブクエリにしているのは、DELETE 自体に LIMIT を付けられる
            # SQLite ビルド(SQLITE_ENABLE_UPDATE_DELETE_LIMIT)が保証されていないため。
            with get_db_cursor(commit=True) as cur:
                # 上記と同じ理由で B608 を抑制する(識別子はリテラル、値はパラメータ化済み)。
                cur.execute(
                    f"DELETE FROM {target.table} WHERE rowid IN ("  # nosec B608
                    f"SELECT rowid FROM {target.table} WHERE {col} < ? "
                    f"ORDER BY {col} LIMIT ?)",
                    (plan.cutoff, chunk),
                )
                affected = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

            deleted += affected
            remaining -= affected
            if affected == 0:
                # 想定より対象が少なかった(並行して消えた等)。無限ループを避ける。
                break

        if deleted:
            logger.info(
                f"🗑️ retention: {target.table} から {deleted} 行削除 "
                f"(保持 {plan.retention_days} 日 / 境界 {plan.cutoff})"
            )
        results.append(TableResult(target=target, deleted_rows=deleted, capped=capped))

    return results


def recent_backup(within_hours: int | None = None) -> tuple[bool, str]:
    """直近のDBバックアップが `within_hours` 以内に存在するかを返す。

    行削除は不可逆なので、#733 の副作用の項が「削除前に必ずバックアップが成功して
    いることを確認」を必須としている。`backup_service.perform_backup` は成功時に
    `config.DB_BACKUPS_DIR` へ `home_system_<timestamp>.db` を置くので、
    そのファイルの更新時刻を成功の記録として使う(専用の状態ファイルを増やさない)。
    """
    hours = (
        within_hours if within_hours is not None
        else int(config.DB_ROW_RETENTION_REQUIRE_BACKUP_WITHIN_HOURS)
    )
    backups_dir = getattr(config, "DB_BACKUPS_DIR", "")
    if not backups_dir or not os.path.isdir(backups_dir):
        return False, f"バックアップ先が見つかりません: {backups_dir or '(未設定)'}"

    cutoff = time.time() - hours * 3600
    # 番兵を 0.0 にすると mtime が epoch(0) のファイルを「見つからなかった」と
    # 誤判定し、「古いバックアップしかない」を「バックアップが無い」と報告してしまう。
    newest_mtime = float("-inf")
    newest_name = ""
    for name in os.listdir(backups_dir):
        if not (name.startswith("home_system_") and name.endswith(".db")):
            continue
        path = os.path.join(backups_dir, name)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime > newest_mtime:
            newest_mtime, newest_name = mtime, name

    if not newest_name:
        return False, f"{backups_dir} にDBバックアップがありません"
    if newest_mtime < cutoff:
        age_h = (time.time() - newest_mtime) / 3600
        return False, f"最新のバックアップ {newest_name} が {age_h:.1f} 時間前で、{hours} 時間以内にありません"
    return True, newest_name


def reclaimable_bytes() -> int:
    """`VACUUM` で回収できる見込みのバイト数(freelist のページ数 × ページサイズ)。

    `DELETE` してもファイルは縮まないため、「消したのにサイズが減らない」ことを
    運用者が誤解しないよう、回収可能量を明示できるようにしておく。
    `VACUUM` 自体はここでは実行しない(DB 全体を書き直すため所要時間と必要な
    空き容量が実機のファイルサイズ次第で、自動で走らせてよい操作ではない)。
    `tools/db_retention.py --vacuum` が空き容量を確認したうえで手動実行する。
    """
    try:
        with get_ro_connection() as conn:
            free_pages = conn.execute("PRAGMA freelist_count").fetchone()[0]
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        return int(free_pages) * int(page_size)
    except sqlite3.Error as e:
        logger.warning(f"retention: freelist の取得に失敗しました: {e}")
        return 0


def build_report(now=None) -> dict[str, Any]:
    """DB 内の全テーブルの行数と、削除対象テーブルの削除予定件数をまとめて返す。

    削除対象に**入れていない**テーブルも行数を出すのは、「どのテーブルを対象に
    追加すべきか」を実測で決めるための材料にするため。#733 の保留理由が
    「何をどれだけ消すかがコードから機械的に導けない」だったので、
    その判断材料を出すところまでを仕組みに含める。
    """
    plans = plan_deletions(now)
    managed = {p.target.table for p in plans}

    tables: list[dict[str, Any]] = []
    with get_ro_connection() as conn:
        names = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            if not r[0].startswith(_SQLITE_INTERNAL_PREFIX)
        ]
        for name in names:
            try:
                # name は sqlite_master 由来(外部入力ではない)で、二重引用符で
                # 引用子として囲んでいる。上記と同じ B608 の誤検知。
                rows = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]  # nosec B608
            except sqlite3.Error as e:
                logger.warning(f"retention: {name} の行数取得に失敗しました: {e}")
                continue
            tables.append({"table": name, "rows": rows, "managed": name in managed})

    db_path = config.SQLITE_DB_PATH
    try:
        db_bytes = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    except OSError:
        db_bytes = 0

    return {
        "db_path": db_path,
        "db_bytes": db_bytes,
        "reclaimable_bytes": reclaimable_bytes(),
        "enabled": bool(config.DB_ROW_RETENTION_ENABLED),
        "tables": tables,
        "plans": plans,
    }


def run_db_retention(now=None) -> RetentionOutcome:
    """保持期間削除の入口。`nas_monitor.run_retention_cleanup` から1日1回呼ばれる。

    `config.DB_ROW_RETENTION_ENABLED` が False(既定)のあいだは**1行も削除しない**。
    削除予定件数を数えてログに残すだけで、実機で `tools/db_retention.py --report`
    を見て有効化するまでは何も起きない。
    """
    plans = plan_deletions(now)
    outcome = RetentionOutcome(dry_run=not config.DB_ROW_RETENTION_ENABLED, plans=plans)

    if outcome.dry_run:
        if outcome.planned_rows:
            logger.info(
                f"🧪 retention(ドライラン): {outcome.planned_rows} 行が削除対象です。"
                f"有効化するには .env に DB_ROW_RETENTION_ENABLED=true を設定してください。"
            )
        return outcome

    ok, detail = recent_backup()
    if not ok:
        # バックアップが無いときは「消さない」側へ倒す。行削除は不可逆で、
        # バックアップ失敗のほうが先に通知される(backup_service が error チャンネルへ送る)。
        outcome.skipped_reason = f"直近のバックアップを確認できないため削除を見送りました({detail})"
        logger.warning(f"⚠️ retention: {outcome.skipped_reason}")
        return outcome

    outcome.results = apply_deletions(plans)
    outcome.reclaimable_bytes = reclaimable_bytes()
    return outcome


def format_summary_lines(outcome: RetentionOutcome) -> list[str]:
    """Discord 通知に載せる行を組み立てる(`nas_monitor` のファイル削除と同じ形式)。"""
    lines: list[str] = []
    if outcome.skipped_reason:
        lines.append(f"- DBの行削除: 見送り（{outcome.skipped_reason}）")
        return lines

    if outcome.dry_run:
        if outcome.planned_rows:
            detail = " / ".join(
                f"{p.target.label} {p.deletable_rows}行"
                for p in outcome.plans if p.deletable_rows
            )
            lines.append(f"- DBの行削除(ドライラン): {outcome.planned_rows}行が対象 （{detail}）")
        return lines

    for result in outcome.results:
        if result.deleted_rows:
            suffix = "（上限に到達。残りは翌日）" if result.capped else ""
            lines.append(f"- {result.target.label}: {result.deleted_rows}行{suffix}")
    if lines and outcome.reclaimable_bytes:
        mb = outcome.reclaimable_bytes / (1024 * 1024)
        lines.append(f"- VACUUM で回収可能: 約{mb:.1f}MB（自動では実行しません）")
    return lines
