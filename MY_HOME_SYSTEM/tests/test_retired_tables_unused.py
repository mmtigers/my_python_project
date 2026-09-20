"""退役済みテーブル・重複列がコードから参照されないことの回帰テスト (Issue #746 / AUDIT-017)。

2026年8月のリファクタリング(ボス戦・装備・ギルド・マイレージ・週間ランキングの削除)で
使われなくなったテーブルが、ベースラインスキーマ(`migrations/0000_baseline_schema.sql`)に
残ったままになっている。とくに `users`(`quest_users` の旧版)と `quests`
(`quest_master` の旧版)は現行テーブルと名前が紛らわしく、誤って
`INSERT INTO users (...)` と書いても SQLite はエラーにしないため、**データが
サイレントに行方不明になる**。重複列(`quest_master.days` / `reward_master.desc`)も
同じリスクを持つ(更新すべき `day_of_week` / `description` の代わりに書いても SQL は成功する)。

**（2026-09-20 更新）** 実機で全件の行数を確認した結果、`users` / `quests` は
いずれも 0 行だったため `migrations/0016` で DROP した。同時に、Issue #507 が
リポジトリのスキーマ定義からは外したものの既存DBから落とす経路が無く実機に残っていた
死蔵テーブルのうち 0 行のもの(`quest_tasks` / `quest_status` / `youtube_subscriptions`)も
落とした。`quest_tasks` は `REFERENCES quest_users(id)` という壊れた宣言を持ち、
`PRAGMA foreign_key_check` を DB 全体で実行不能にしていた(Issue #747 の前提を潰していた)。

行を持つテーブルは DROP がデータの破棄になるため引き続き残す。ここでは次の4点を固定する。

1. 行を持つ退役済みテーブル・重複列が**スキーマには存在し続ける**こと(勝手に消えていない)。
2. DROP 済みのテーブルが**スキーマに存在しない**こと(0016 が効いている)。
3. `PRAGMA foreign_key_check` が DB 全体で実行できること。
4. 退役・DROP 済みの双方が**実行コードからは一切参照されない**こと(誤用の CI ゲート)。

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
# 実機で行を持つため DROP を見送ったもの(行数は 2026-09-20 の実測)。
RETIRED_TABLES = [
    "party_state",              # ボス戦 (1行)
    "equipment_master",         # 装備 (16行)
    "user_equipments",          # 装備 (12行)
    "family_mileage",           # マイレージ (1行)
    "family_mileage_history",   # マイレージ (5行)
    "bounties",                 # 賞金クエスト (9行)
    "suumo_records",            # 物件情報収集 (56行)
]

# 実機で 0 行であることを確認して DROP したテーブル(migrations/0016)。
# コードから参照されないことの検査は RETIRED_TABLES と同じく続ける
# (消えた後に誰かが再び `INSERT INTO users (...)` と書くのを止めるため)。
DROPPED_TABLES = [
    "users",                    # quest_users の旧版 (#746)
    "quests",                   # quest_master の旧版 (#746)
    "quest_tasks",              # #507 の死蔵テーブル。壊れたFKで foreign_key_check を塞いでいた
    "quest_status",             # #507 の死蔵テーブル
    "youtube_subscriptions",    # #507 の死蔵テーブル
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


@pytest.mark.parametrize("table", DROPPED_TABLES)
def test_dropped_table_is_absent_from_schema(isolated_db, table):
    """migrations/0016 で DROP したテーブルが、マイグレーション全適用後に存在しないこと。

    ベースライン(0000)は `users` / `quests` を CREATE するため、0016 が
    実際に効いていないとここで検知できる。`quest_tasks` 等の #507 の死蔵テーブルは
    そもそもベースラインに無いので、新規DBでは最初から存在しない。
    """
    with get_db_cursor() as cur:
        row = cur.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
    assert row is None, (
        f"{table} がスキーマに存在します。migrations/0016 が適用されていないか、"
        "どこかで再作成されています。"
    )


def test_foreign_key_check_runs(isolated_db):
    """`PRAGMA foreign_key_check` が DB 全体で実行できること (#747 の前提)。

    `quest_tasks` は `REFERENCES quest_users(id)` と宣言されていたが quest_users に
    `id` 列は無く(主キーは `user_id TEXT`)、この1テーブルのせいで
    `PRAGMA foreign_key_check` が DB 全体で `foreign key mismatch` を送出し、
    参照整合性の検査そのものができなかった。0016 の DROP で解消したことを固定する。
    """
    with get_db_cursor() as cur:
        violations = cur.execute("PRAGMA foreign_key_check").fetchall()
    assert not violations, f"参照整合性の違反があります: {violations}"


@pytest.mark.parametrize("table", RETIRED_TABLES + DROPPED_TABLES)
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
