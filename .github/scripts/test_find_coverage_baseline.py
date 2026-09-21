# .github/scripts/test_find_coverage_baseline.py
"""find_coverage_baseline.sh の回帰テスト。

2026-09-21(PR #839)に、ラチェットの比較元として**11日前の master run** が選ばれ、
backend は実測 86.00% に対して 74.76%、frontend は 78.64% に対して 47.22% と
比較していた事故がある。当時この処理は test.yml の2ジョブへ直接コピーされており、
テストも無かったため、ログを人間が読むまで誰も気づけなかった。

ここでは `gh` を偽物に差し替えて、スクリプトが

  - 成功 run のうち **created_at が最新のもの** を選ぶこと(サーバーの並び順に依存しない)
  - 失敗・キャンセルした run を比較元にしないこと
  - 成功 run が1件も無ければ run_id を空にすること(呼び出し側がスキップする)
  - 比較元が古いときに ::warning:: を出すこと(黙って誤った値と比較しない)

を検証する。CI では test.yml の lint ジョブから `pytest .github/scripts/` で実行される。
"""
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "find_coverage_baseline.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("jq") is None, reason="jq が必要(CI の ubuntu-latest には入っている)"
)


def _run(tmp_path, runs, *, env=None):
    """偽 `gh` を PATH の先頭に置いてスクリプトを実行し、(returncode, stdout, run_id) を返す。"""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    payload = tmp_path / "runs.json"
    payload.write_text(json.dumps({"workflow_runs": runs}), encoding="utf-8")

    # `gh api ... --jq '<式>'` だけを受け付ける最小の偽物。実物と同じく jq へ通す。
    fake_gh = bindir / "gh"
    fake_gh.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/bash
            # 最後の引数が --jq の式
            expr="${{@: -1}}"
            jq -r "$expr" {payload}
            """
        ),
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)

    output_file = tmp_path / "github_output"
    output_file.touch()
    full_env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_OUTPUT": str(output_file),
        **(env or {}),
    }
    proc = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, env=full_env
    )
    run_id = ""
    for line in output_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("run_id="):
            run_id = line.split("=", 1)[1]
    return proc, run_id


def _run_entry(run_id, created_at, conclusion="success"):
    return {"id": run_id, "created_at": created_at, "conclusion": conclusion}


def _recent(days_ago=0):
    """今日から days_ago 日前の created_at(テストを実時刻に依存させないため相対で作る)。"""
    import datetime

    stamp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_picks_newest_successful_run(tmp_path):
    proc, run_id = _run(
        tmp_path,
        [
            _run_entry(111, _recent(0)),
            _run_entry(222, _recent(1)),
        ],
    )
    assert proc.returncode == 0, proc.stderr
    assert run_id == "111"


def test_sorts_by_created_at_instead_of_trusting_api_order(tmp_path):
    """API が降順で返さなくても最新を選ぶこと。

    2026-09-21 の事故では 11日前の run が先頭に来ていた。サーバーの並びを信用しない。
    """
    proc, run_id = _run(
        tmp_path,
        [
            _run_entry(999, _recent(11)),  # API が先頭に返してきた古い run
            _run_entry(111, _recent(0)),
            _run_entry(222, _recent(1)),
        ],
    )
    assert proc.returncode == 0, proc.stderr
    assert run_id == "111", "created_at の降順で選び直していない"


def test_ignores_failed_and_cancelled_runs(tmp_path):
    proc, run_id = _run(
        tmp_path,
        [
            _run_entry(111, _recent(0), conclusion="failure"),
            _run_entry(222, _recent(1), conclusion="cancelled"),
            _run_entry(333, _recent(2), conclusion="success"),
        ],
    )
    assert proc.returncode == 0, proc.stderr
    assert run_id == "333"


def test_emits_empty_run_id_when_no_successful_run(tmp_path):
    """成功 run が無ければ空。呼び出し側はこれを見て比較をスキップする。"""
    proc, run_id = _run(tmp_path, [_run_entry(111, _recent(0), conclusion="failure")])
    assert proc.returncode == 0, proc.stderr
    assert run_id == ""
    assert "(none)" in proc.stdout


def test_warns_when_baseline_is_stale(tmp_path):
    """古い比較元は警告する(黙って誤った値と比較しない)。"""
    proc, run_id = _run(tmp_path, [_run_entry(999, _recent(11))])
    assert proc.returncode == 0, proc.stderr
    assert run_id == "999"
    assert "::warning::" in proc.stdout
    assert "11 日前" in proc.stdout


def test_does_not_warn_for_a_fresh_baseline(tmp_path):
    proc, _ = _run(tmp_path, [_run_entry(111, _recent(0))])
    assert proc.returncode == 0, proc.stderr
    assert "::warning::" not in proc.stdout


def test_stale_threshold_is_configurable(tmp_path):
    proc, _ = _run(tmp_path, [_run_entry(999, _recent(5))], env={"STALE_AFTER_DAYS": "10"})
    assert proc.returncode == 0, proc.stderr
    assert "::warning::" not in proc.stdout
