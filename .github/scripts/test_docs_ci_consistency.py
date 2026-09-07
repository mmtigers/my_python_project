# .github/scripts/test_docs_ci_consistency.py
"""ドキュメントに手書きされた CI の事実が test.yml の実態と一致するかの検証。

カバレッジ閾値(`--cov-fail-under=NN`)は test.yml が正であり、CLAUDE.md / README /
runbook はそれを転記している。転記は人手のため、閾値を引き上げるたびに
ずれてきた(2026-09: runbook が 67 のまま CI は 70)。.env.example 整合テストや
仕様書件数テスト(test_spec_readme_counts.py)と同じく、機械的に固定する。

失敗したときは、エラーメッセージが示す test.yml の値へドキュメント側を書き換えること
(閾値を変える PR では、その更新も同じ PR に含める)。

CI では test.yml の lint ジョブから `pytest .github/scripts/` で実行される。
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test.yml"

# 閾値を転記しているドキュメント(glob)。新たに転記する文書を増やしたらここにも追加する。
DOC_GLOBS = [
    "CLAUDE.md",
    "README.md",
    "MY_HOME_SYSTEM/README.md",
    "docs/runbooks/*.md",
]

_COV_RE = re.compile(r"--cov-fail-under[=\s](\d+)")


def _workflow_threshold() -> int:
    values = {int(v) for v in _COV_RE.findall(WORKFLOW.read_text(encoding="utf-8"))}
    assert len(values) == 1, f"test.yml 内の --cov-fail-under が一意ではありません: {sorted(values)}"
    return values.pop()


def _doc_files():
    for pattern in DOC_GLOBS:
        yield from sorted(REPO_ROOT.glob(pattern))


def test_documented_coverage_threshold_matches_workflow():
    expected = _workflow_threshold()
    mismatches = []
    for path in _doc_files():
        for value in _COV_RE.findall(path.read_text(encoding="utf-8")):
            if int(value) != expected:
                mismatches.append(f"{path.relative_to(REPO_ROOT)}: --cov-fail-under={value}")
    assert not mismatches, (
        f"test.yml のカバレッジ閾値は {expected} ですが、ドキュメント側の記載がずれています: "
        f"{mismatches} — 該当箇所を {expected} に書き換えてください。"
    )


def test_at_least_one_doc_mentions_threshold():
    """このテストが空振り(転記箇所ゼロ)になっていないことの自己検証。"""
    found = any(_COV_RE.search(p.read_text(encoding="utf-8")) for p in _doc_files())
    assert found, "閾値を転記しているドキュメントが見つかりません(DOC_GLOBS を見直してください)"
