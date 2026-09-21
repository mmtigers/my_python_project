# .github/scripts/test_docs_ci_consistency.py
"""ドキュメントに手書きされた CI の事実が test.yml の実態と一致するかの検証。

カバレッジ閾値(`--cov-fail-under=NN`)は test.yml が正であり、CLAUDE.md / README /
runbook はそれを転記している。転記は人手のため、閾値を引き上げるたびに
ずれてきた(2026-09: runbook が 67 のまま CI は 70)。.env.example 整合テストや
仕様書件数テスト(test_spec_readme_counts.py)と同じく、機械的に固定する。

閾値は当初 MY_HOME_SYSTEM の1つだけだったが、DDD 側にも床を設けたため
「test.yml 内の値は一意」という前提は成り立たなくなった。そのため test.yml を
YAML として読み、各ステップの working-directory(ステップ > ジョブ defaults >
ワークフロー defaults の順で解決)ごとに閾値を集める。

ドキュメント側も同様に、記載を**どのサブシステムのものか**まで見て突き合わせる。
PR #839 で複数サブシステム対応にした直後は「ドキュメント中の値が、いずれかの
サブシステムの閾値の集合に含まれるか」という緩い判定になっており、たとえば
DDD のつもりで MY_HOME_SYSTEM の値(82)を書いても検知できなかった
(同PRのレビューで指摘された)。ここでは各記載の**直前に現れるサブシステム名**で
帰属先を決め、その値と厳密に比較する。帰属先を決められない記載は
「どのサブシステムの話か読み手にも分からない」ことを意味するので失敗させる。

失敗したときは、エラーメッセージが示す test.yml の値へドキュメント側を書き換えること
(閾値を変える PR では、その更新も同じ PR に含める)。

CI では test.yml の lint ジョブから `pytest .github/scripts/` で実行される。
"""
import re
from pathlib import Path

import yaml

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
# 記載の帰属先を決めるためのサブシステム名。ドキュメント中でその記載より前に
# 最後に現れたものを、その記載が指すサブシステムとみなす。
_SUBSYSTEM_RE = re.compile(r"MY_HOME_SYSTEM|DDD")


def _normalize_workdir(value: str) -> str:
    """`./DDD` や `DDD/` を `DDD` に揃える(ルートは `.`)。"""
    cleaned = value.strip().strip("/").removeprefix("./")
    return cleaned or "."


def _workflow_thresholds() -> dict[str, int]:
    """test.yml のステップから {working-directory: 閾値} を集める。

    同じディレクトリに対して複数の異なる閾値が書かれていたら、どれが正か判断
    できないので失敗させる(従来の「test.yml 内で一意」の assert を、
    ディレクトリ単位へ置き換えたもの)。
    """
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    workflow_default = _normalize_workdir(
        ((workflow.get("defaults") or {}).get("run") or {}).get("working-directory") or "."
    )

    thresholds: dict[str, set[int]] = {}
    for job in (workflow.get("jobs") or {}).values():
        job_default = _normalize_workdir(
            ((job.get("defaults") or {}).get("run") or {}).get("working-directory")
            or workflow_default
        )
        for step in job.get("steps") or []:
            script = step.get("run")
            if not script:
                continue
            workdir = _normalize_workdir(step.get("working-directory") or job_default)
            for value in _COV_RE.findall(script):
                thresholds.setdefault(workdir, set()).add(int(value))

    conflicting = {d: sorted(v) for d, v in thresholds.items() if len(v) > 1}
    assert not conflicting, f"test.yml 内で同一ディレクトリに複数の --cov-fail-under があります: {conflicting}"
    return {directory: values.pop() for directory, values in thresholds.items()}


def _doc_files():
    for pattern in DOC_GLOBS:
        yield from sorted(REPO_ROOT.glob(pattern))


def test_workflow_defines_thresholds_for_both_python_subsystems():
    """MY_HOME_SYSTEM と DDD の双方に床があること(片方だけ外れた退行の検知)。"""
    thresholds = _workflow_thresholds()
    missing = {"MY_HOME_SYSTEM", "DDD"} - thresholds.keys()
    assert not missing, (
        f"test.yml に --cov-fail-under が無いサブシステムがあります: {sorted(missing)} — "
        "カバレッジの床はラチェット(比較元が取れないときはスキップされる)の"
        "フォールバックなので、両方に必要です。"
    )


def _documented_thresholds():
    """ドキュメント中の記載を (パス, 行番号, 帰属サブシステム, 値) で列挙する。

    帰属は「その記載より前に最後に現れたサブシステム名」で決める。素朴だが、
    このリポジトリの書き方(`### MY_HOME_SYSTEM (バックエンド)` の見出し配下の
    コマンド例、`` `DDD` のpytestも…床 `--cov-fail-under=74` `` のような文)では
    すべて正しく解決できる。解決できない(サブシステム名が一度も出てこない)場合は
    None を返し、呼び出し側が失敗させる。
    """
    for path in _doc_files():
        text = path.read_text(encoding="utf-8")
        for match in _COV_RE.finditer(text):
            names = _SUBSYSTEM_RE.findall(text[: match.start()])
            line_no = text.count("\n", 0, match.start()) + 1
            yield path, line_no, (names[-1] if names else None), int(match.group(1))


def test_documented_coverage_threshold_matches_workflow():
    expected = _workflow_thresholds()
    mismatches = []
    for path, line_no, subsystem, value in _documented_thresholds():
        where = f"{path.relative_to(REPO_ROOT)}:{line_no}"
        if subsystem is None:
            mismatches.append(
                f"{where}: --cov-fail-under={value} がどのサブシステムの値か判別できません"
            )
        elif subsystem not in expected:
            mismatches.append(
                f"{where}: --cov-fail-under={value} の帰属先 {subsystem} が test.yml にありません"
            )
        elif expected[subsystem] != value:
            mismatches.append(
                f"{where}: {subsystem} の閾値は {expected[subsystem]} ですが "
                f"--cov-fail-under={value} と書かれています"
            )
    assert not mismatches, (
        f"test.yml のカバレッジ閾値は {expected} です。ドキュメント側の記載がずれています:\n"
        + "\n".join("  - " + m for m in mismatches)
        + "\n該当箇所を書き換えるか、どのサブシステムの値かが直前で分かるようにしてください。"
    )


def test_every_workflow_threshold_is_documented():
    """CI 側に閾値を足したらドキュメントにも転記されること(サブシステム単位で)。"""
    expected = _workflow_thresholds()
    documented = {subsystem for _, _, subsystem, _ in _documented_thresholds()}
    missing = {d: v for d, v in expected.items() if d not in documented}
    assert not missing, (
        f"test.yml にあるのにドキュメントへ転記されていない閾値があります: {missing} — "
        f"{DOC_GLOBS} のいずれかに、直前でサブシステム名が分かる形で記載してください。"
    )


def test_at_least_one_doc_mentions_threshold():
    """このテストが空振り(転記箇所ゼロ)になっていないことの自己検証。"""
    found = any(_COV_RE.search(p.read_text(encoding="utf-8")) for p in _doc_files())
    assert found, "閾値を転記しているドキュメントが見つかりません(DOC_GLOBS を見直してください)"
