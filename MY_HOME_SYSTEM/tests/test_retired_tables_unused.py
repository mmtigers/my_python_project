"""退役済みテーブル・重複列がコードから参照されないことの回帰テスト (Issue #746 / AUDIT-017)。

2026年8月のリファクタリング(ボス戦・装備・ギルド・マイレージ・週間ランキングの削除)で
使われなくなったテーブルが、ベースラインスキーマ(`migrations/0000_baseline_schema.sql`)に
残ったままになっている。とくに `users`(`quest_users` の旧版)と `quests`
(`quest_master` の旧版)は現行テーブルと名前が紛らわしく、誤って
`INSERT INTO users (...)` と書いても SQLite はエラーにしないため、**データが
サイレントに行方不明になる**。重複列(`quest_master.days` / `reward_master.desc`)も
同じリスクを持つ(更新すべき `day_of_week` / `description` の代わりに書いても SQL は成功する)。

DROP は不可逆で、実機の行数確認・バックアップ確認が前提になるため本テストの範囲外
(Issue #746 の「推奨修正」)。ここでは次の2点だけを固定する。

1. 退役済みテーブル・重複列が**スキーマには存在し続ける**こと(勝手に消えていない)。
2. それらが**実行コードからは一切参照されない**こと(誤用の CI ゲート)。

退役の一覧と方針は `migrations/README.md` の「退役済みテーブル・重複列」節を正とする。
"""
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import get_db_cursor

REPO_ROOT = Path(__file__).resolve().parents[2]
MY_HOME_SYSTEM = REPO_ROOT / "MY_HOME_SYSTEM"

# 退役済み(スキーマに残るがコードからは使われない)テーブル。
RETIRED_TABLES = [
    "users",                    # quest_users の旧版
    "quests",                   # quest_master の旧版
    "party_state",              # ボス戦
    "equipment_master",         # 装備
    "user_equipments",          # 装備
    "family_mileage",           # マイレージ
    "family_mileage_history",   # マイレージ
    "bounties",                 # 賞金クエスト
    "suumo_records",            # 物件情報収集
]

# 退役済みの重複列: (テーブル, 使われていない列, 実際に使われている列)
RETIRED_COLUMNS = [
    ("quest_master", "days", "day_of_week"),
    ("reward_master", "desc", "description"),
]

# SQL 中でテーブル名が現れる位置(FROM / INTO / UPDATE / JOIN / TABLE の直後)だけを見る。
# 単純な単語検索では `quest_users` の一部や日本語コメント中の "users" まで拾ってしまう。
_SQL_CONTEXT = r"(?:from|into|update|join|table)\s+[\"'`\[]?%s\b"


def _source_files():
    """実行コード(テスト・マイグレーション・生成物を除く)を列挙する。"""
    for base in (MY_HOME_SYSTEM, REPO_ROOT / "DDD"):
        for path in sorted(base.rglob("*.py")):
            parts = path.relative_to(REPO_ROOT).parts
            if "tests" in parts or "migrations" in parts or ".venv" in parts:
                continue
            yield path
        for path in sorted(base.rglob("*.sh")):
            yield path


@pytest.mark.parametrize("table", RETIRED_TABLES)
def test_retired_table_exists_in_schema(isolated_db, table):
    """退役済みテーブルは(DROP せず)スキーマに残っている、という現状を固定する。

    もしここが落ちたなら DROP マイグレーションが入ったということなので、
    本ファイルと `migrations/README.md` の一覧からその行を外すこと。
    """
    with get_db_cursor() as cur:
        row = cur.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
    assert row is not None, (
        f"退役済みテーブル {table} がスキーマから消えています。DROP したのであれば "
        "tests/test_retired_tables_unused.py と migrations/README.md の一覧も更新してください。"
    )


@pytest.mark.parametrize("table,retired,current", RETIRED_COLUMNS)
def test_retired_column_and_its_replacement_exist(isolated_db, table, retired, current):
    """重複列は両方ともスキーマに残っている(使うのは current の側)。"""
    with get_db_cursor() as cur:
        columns = {r["name"] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}
    assert retired in columns, f"{table}.{retired} がスキーマから消えています。一覧を更新してください。"
    assert current in columns, f"{table}.{current} が存在しません({table}.{retired} の移行先)。"


@pytest.mark.parametrize("table", RETIRED_TABLES)
def test_retired_table_is_not_referenced_by_code(table):
    """退役済みテーブルを実行コードの SQL が参照していないこと。

    `users` / `quests` は現行の `quest_users` / `quest_master` と紛らわしく、
    誤って書いても SQLite はエラーにしない(サイレントにデータが失われる)ため、
    CI で誤用を止める。
    """
    pattern = re.compile(_SQL_CONTEXT % re.escape(table), re.IGNORECASE)
    offenders = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{i}: {line.strip()}")
    assert not offenders, (
        f"退役済みテーブル {table} を参照しているコードがあります。"
        f"現行テーブル(quest_users / quest_master 等)の誤りではないか確認してください:\n"
        + "\n".join(offenders)
    )
