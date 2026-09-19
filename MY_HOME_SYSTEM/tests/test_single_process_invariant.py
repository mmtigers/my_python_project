"""単一プロセス前提(AUDIT-031 / Issue #760)の回帰テスト。

`quest_users`(gold / exp / level / medal_count)の read-modify-write の正しさは、
`services/quest/locks.py` の `_get_user_balance_lock`(= `threading.Lock`)による
**プロセス内の直列化だけ**で成立している。`BEGIN IMMEDIATE` 等の DB レベルの排他は
リポジトリ内の実行コードに存在しない(#544 で入り #547 で撤去。この不変条件自体は
`tests/test_balance_lock_cross_path_concurrency.py` の
`TestConcurrencyControlDocumentationMatchesCode` が固定している)。

したがって `unified_server` を複数プロセスで動かすと、`threading.Lock` が共有されない
ためロストアップデートが起きる。しかも **エラーは出ず、ゴールドが静かに消える/増える**
ため、テストでも運用でも気づけない。

このファイルは「プロセスを増やす変更」を機械的に検知するためのもので、次の2つの
入口を塞ぐ:

1. `uvicorn.run(...)` に `workers` を渡す(= uvicorn 自身がワーカーを fork する)
2. `gunicorn` を依存に入れる(= `-w N` でワーカーを増やす典型的な経路)

systemd 側の `ExecStart` に `--workers` が現れないことは
`.github/scripts/test_systemd_units.py` が検査する。

この前提を意図的に変える場合は、先に `quest_users` の更新を DB レベルで原子的な形
(条件付き UPDATE / 楽観ロック / `BEGIN IMMEDIATE`)へ移し、本ファイルと CLAUDE.md の
「データベース」節、`deploy/systemd/home_system.service` のコメントを同時に更新すること。
"""
import ast
import os

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
UNIFIED_SERVER = os.path.join(BACKEND_DIR, "unified_server.py")


def _uvicorn_run_calls(tree: ast.AST) -> list[ast.Call]:
    """`uvicorn.run(...)` / `run(...)` の呼び出しノードを列挙する。"""
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "run":
            # uvicorn.run(...) の形
            if isinstance(func.value, ast.Name) and func.value.id == "uvicorn":
                calls.append(node)
        elif isinstance(func, ast.Name) and func.id == "run":
            # from uvicorn import run した場合の run(...) の形
            calls.append(node)
    return calls


def test_uvicorn_is_not_started_with_multiple_workers():
    """`uvicorn.run` に `workers` を渡していないこと。

    文字列の部分一致ではなく AST でキーワード引数を見るため、コメントや
    docstring 中の "workers" という語(既存のテストが使う `max_workers` を含む)で
    誤検知しない。
    """
    with open(UNIFIED_SERVER, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=UNIFIED_SERVER)

    offenders = []
    for call in _uvicorn_run_calls(tree):
        for kw in call.keywords:
            # **kwargs で渡された場合(kw.arg is None)も、静的には中身が追えないため拒否する
            if kw.arg is None or kw.arg == "workers":
                offenders.append(f"unified_server.py:{call.lineno} (keyword={kw.arg})")

    assert not offenders, (
        "uvicorn.run に workers(または **kwargs)を指定してはなりません。"
        "quest_users の read-modify-write は services/quest/locks.py の "
        "threading.Lock でしか直列化されておらず、プロセスを増やすと "
        "ロストアップデートがエラー無しで発生します:\n" + "\n".join(offenders)
    )


def test_gunicorn_is_not_a_dependency():
    """`gunicorn` が依存に入っていないこと(`-w N` でワーカーを増やす典型的な経路)。"""
    offenders = []
    for name in ("requirements.in", "requirements.txt", "requirements-dev.in", "requirements-dev.txt"):
        path = os.path.join(BACKEND_DIR, name)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            for lineno, raw in enumerate(f, start=1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                # "gunicorn" / "gunicorn==21.2.0" / "gunicorn>=20" のいずれにも当てる
                package = line.split("==")[0].split(">=")[0].split("<")[0].split("[")[0].strip()
                if package.lower() == "gunicorn":
                    offenders.append(f"{name}:{lineno}")

    assert not offenders, (
        "gunicorn を依存に追加してはなりません(単一プロセス前提。"
        "本ファイルの docstring を参照):\n" + "\n".join(offenders)
    )
