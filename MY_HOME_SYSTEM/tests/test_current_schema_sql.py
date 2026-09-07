# MY_HOME_SYSTEM/tests/test_current_schema_sql.py
"""
current_schema.sql が migrations/ からの生成結果と一致することを検証する。

current_schema.sql はコードから実行されない参考ドキュメントのため、マイグレーションを
追加しても更新を忘れて何のエラーも起きなかった(Issue #115)。以前は「ALTER TABLE で
追加された列が含まれているか」の一方向チェックと、手で維持する既知差分の許容リスト
(Issue #411/#543)で守っていたが、本ファイルは `init_unified_db.py --dump-schema`
による生成物になったため、「ファイルの内容 == 再生成結果」の完全一致で検証する。
許容リストの維持は不要になった。

失敗したら MY_HOME_SYSTEM/ で `python init_unified_db.py --dump-schema` を実行し、
再生成された current_schema.sql をコミットすること。
"""
import os
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SCHEMA_FILE = os.path.join(BASE_DIR, "current_schema.sql")
sys.path.append(BASE_DIR)

import init_unified_db  # noqa: E402


def test_current_schema_sql_matches_generated_dump():
    with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
        committed = f.read()
    generated = init_unified_db.dump_schema_sql()
    assert committed == generated, (
        "current_schema.sql が migrations/ の再生成結果と一致しません。"
        "MY_HOME_SYSTEM/ で `python init_unified_db.py --dump-schema` を実行して再生成し、コミットしてください。"
    )


def test_generated_dump_reflects_all_migrations():
    """再生成結果に schema_migrations と、最後のマイグレーションが追加した列が含まれること
    (生成関数自体が migrations/ を全適用していることの自己検証)。"""
    generated = init_unified_db.dump_schema_sql()
    assert "CREATE TABLE schema_migrations" in generated
    assert "medals_earned" in generated  # 0009_add_quest_history_medals_earned.sql
    assert "nas_usage_percent" in generated  # 0006_add_device_records_nas_usage_percent.sql
    assert generated.startswith("-- このファイルは生成物です")


def test_dump_schema_does_not_touch_configured_db(tmp_path, monkeypatch):
    """生成は :memory: で完結し、config.SQLITE_DB_PATH のファイルを作成・変更しないこと。"""
    import config
    db_path = tmp_path / "should_not_be_created.db"
    monkeypatch.setattr(config, "SQLITE_DB_PATH", str(db_path))
    init_unified_db.dump_schema_sql()
    assert not db_path.exists()


def test_write_current_schema_and_cli(tmp_path):
    out = tmp_path / "schema.sql"
    assert init_unified_db.main(["--dump-schema", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == init_unified_db.dump_schema_sql()
