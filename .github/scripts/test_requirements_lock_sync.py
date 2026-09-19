# .github/scripts/test_requirements_lock_sync.py
"""`requirements.in` と lock (`requirements.txt`) の整合検証 (Issue #744 / AUDIT-015)。

## なぜ必要か

CLAUDE.md は「依存の追加・変更は `requirements.in` / `requirements-dev.in` を編集し、
`pip-compile` で lock を再生成する(手で編集しない。Issue #647)」と規定しているが、
これを検証する仕組みが CI に1つも無かった。次の2方向のどちらでも CI は緑のまま壊れる。

1. `requirements.in` だけ編集して `pip-compile` を忘れる
   → 実機は古い lock でインストールするため**追加した依存が入らない**
     (Issue #736 と同じ失敗モード: 実機の .venv がどのファイルからも再現できない)
2. `requirements.txt` を手で編集する
   → 次に誰かが `pip-compile` を実行した瞬間に手編集が消える。Issue #647 が解決した
     「Dependabot が pydantic と pydantic_core を独立に最新化して ResolutionImpossible
     になる」状態が再発しうる

`requirements.in` は約40行、`requirements.txt` は約280行の lock であり、**この乖離は
目視では検出できない**。同じ「生成物の整合を機械的に担保する」テストは
`tests/test_current_schema_sql.py`(current_schema.sql) ・
`tests/test_env_example_consistency.py`(.env.example) ・
`test_docs_ci_consistency.py`(--cov-fail-under) と既にあり、lock だけが漏れていた。

## 何を検証するか

**ネットワークを使わない構造的な検査**に限定する:

- `.in` に書いた直接依存がすべて lock に pin されており、かつ lock 側で
  `# via -r <その .in>` と注記されていること(= pip-compile がその行を見て解決したこと)
- 逆に、lock で `# via -r <.in>` と注記されているのに `.in` に無いものが残っていないこと

`pip-compile` を実際に実行して差分を取る検査は**ネットワークが要る**ため、CI の
lint ジョブ側のステップ(`requirements lock is in sync with requirements.in`)が行う。
本テストはその前段で、ローカルでも `python -m pytest .github/scripts/` だけで
「pip-compile 忘れ」の大半を検出できるようにするためのもの。
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "MY_HOME_SYSTEM"

# (直接依存を書く .in, 生成される lock)
LOCK_PAIRS = [
    (BACKEND / "requirements.in", BACKEND / "requirements.txt"),
    (BACKEND / "requirements-dev.in", BACKEND / "requirements-dev.txt"),
]

# "package", "package[extra]", "package>=1.2", "package ; python_version<'3.12'" から
# パッケージ名だけを取り出す
_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
# lock 側の pin 行 ("package==1.2.3")
_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==")


def _normalize(name: str) -> str:
    """PEP 503 の正規化(pip-compile は lock 側でこの形に揃える)。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def _direct_requirements(in_path: Path) -> set:
    names = set()
    for raw in in_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        # 空行・コメント・オプション行(-c / -r / --hash 等)は直接依存ではない
        if not line or line.startswith(("#", "-")):
            continue
        line = line.split("#", 1)[0].strip()
        m = _NAME_RE.match(line)
        if m:
            names.add(_normalize(m.group(1)))
    return names


def _locked_direct_requirements(lock_path: Path, in_name: str) -> set:
    """lock のうち `# via -r <in_name>` が付いている(=直接依存として解決された)もの。"""
    names = set()
    current = None
    marker = f"-r {in_name}"
    for raw in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        m = _PIN_RE.match(line)
        if m:
            current = _normalize(m.group(1))
            continue
        if current and line.startswith("#") and marker in line:
            names.add(current)
    return names


def _all_pinned(lock_path: Path) -> set:
    return {
        _normalize(m.group(1))
        for m in (_PIN_RE.match(ln.strip()) for ln in lock_path.read_text(encoding="utf-8").splitlines())
        if m
    }


@pytest.mark.parametrize("in_path,lock_path", LOCK_PAIRS, ids=lambda p: p.name)
def test_every_direct_requirement_is_pinned_in_the_lock(in_path, lock_path):
    """`.in` に書いた依存が lock に pin されていること(pip-compile 忘れの検出)。"""
    missing = sorted(_direct_requirements(in_path) - _all_pinned(lock_path))
    assert not missing, (
        f"{in_path.name} に書かれているのに {lock_path.name} に pin されていない依存があります: "
        f"{missing} — `pip-compile --strip-extras --no-emit-index-url "
        f"--output-file {lock_path.name} {in_path.name}` を実行してコミットしてください。"
    )


@pytest.mark.parametrize("in_path,lock_path", LOCK_PAIRS, ids=lambda p: p.name)
def test_lock_direct_annotations_match_the_in_file(in_path, lock_path):
    """lock 側の `# via -r <.in>` 注記と `.in` の内容が双方向に一致すること。

    片方向(`.in` → lock)だけだと「`.in` から消したのに lock に直接依存として
    残っている」ケースを取りこぼす。
    """
    declared = _direct_requirements(in_path)
    annotated = _locked_direct_requirements(lock_path, in_path.name)

    missing = sorted(declared - annotated)
    stale = sorted(annotated - declared)
    assert not missing and not stale, (
        f"{in_path.name} と {lock_path.name} の直接依存が一致しません。\n"
        f"  lock に直接依存として現れないもの: {missing}\n"
        f"  .in に無いのに lock が直接依存としているもの: {stale}\n"
        f"`pip-compile --strip-extras --no-emit-index-url "
        f"--output-file {lock_path.name} {in_path.name}` を実行してコミットしてください。"
    )


@pytest.mark.parametrize("in_path,lock_path", LOCK_PAIRS, ids=lambda p: p.name)
def test_lock_is_generated_by_pip_compile(in_path, lock_path):
    """lock が pip-compile の生成物のままであること(手で書き起こされていないこと)。

    ヘッダの生成コマンドが CLAUDE.md に記載された正規のオプション
    (`--strip-extras --no-emit-index-url`)で生成されていることも併せて固定する。
    オプションが変わると出力形式が変わり、CI の diff ゲートが毎回落ちる。
    """
    head = "\n".join(lock_path.read_text(encoding="utf-8").splitlines()[:6])
    assert "autogenerated by pip-compile" in head, (
        f"{lock_path.name} が pip-compile の生成物ではありません(手編集は Issue #647 で禁止)"
    )
    assert "--strip-extras" in head and "--no-emit-index-url" in head, (
        f"{lock_path.name} の生成コマンドが CLAUDE.md 記載のオプションと異なります: \n{head}"
    )
    assert in_path.name in head, f"{lock_path.name} の生成元が {in_path.name} ではありません"
