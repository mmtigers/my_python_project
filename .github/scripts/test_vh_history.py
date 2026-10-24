"""deploy/claude-skills/verify-handoff/scripts/vh.py の履歴(追記・集計・手動介入の記録)のテスト (#1020)。"""
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VH = ROOT / "deploy/claude-skills/verify-handoff/scripts/vh.py"
_spec = importlib.util.spec_from_file_location("vh_history_under_test", VH)
vh = importlib.util.module_from_spec(_spec)
sys.modules["vh_history_under_test"] = vh
_spec.loader.exec_module(vh)


def _repo(tmp_path, checks):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "verify.json").write_text(json.dumps({"checks": checks}), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def _vh(root, *args):
    return subprocess.run([sys.executable, str(VH), *args, "--root", str(root)], capture_output=True, text=True)


def _history(root):
    return [json.loads(line) for line in (root / ".claude/handoff/history.jsonl").read_text(encoding="utf-8").splitlines()]


def test_run_appends_one_line_per_run_and_does_not_overwrite(tmp_path):
    root = _repo(tmp_path, [{"name": "ok", "cmd": "true"}, {"name": "ng", "cmd": "false", "optional": True}])
    _vh(root, "run")
    _vh(root, "run")
    hist = _history(root)
    assert [h["type"] for h in hist] == ["run", "run"]
    assert [c["name"] for c in hist[0]["checks"]] == ["ok", "ng"]
    assert hist[0]["checks"][1]["status"] == "fail" and hist[0]["checks"][1]["optional"] is True
    assert hist[0]["partial"] is False and hist[0]["only"] is None
    # 出力(tail)は履歴に入れない(肥大化と秘密の混入を避ける)
    assert all("tail" not in c for c in hist[0]["checks"])


def test_partial_run_records_only_this_run_not_merged_results(tmp_path):
    root = _repo(tmp_path, [{"name": "a", "cmd": "true"}, {"name": "b", "cmd": "true"}])
    _vh(root, "run")
    _vh(root, "run", "--only", "b")
    last = _history(root)[-1]
    assert last["partial"] is True and last["only"] == ["b"]
    assert [c["name"] for c in last["checks"]] == ["b"]


def test_history_dir_is_excluded_from_git(tmp_path):
    root = _repo(tmp_path, [{"name": "a", "cmd": "true"}])
    _vh(root, "run")
    status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout
    assert ".claude/handoff" not in status


def test_note_records_intervention_and_rejects_unknown_kind(tmp_path):
    root = _repo(tmp_path, [{"name": "a", "cmd": "true"}])
    assert _vh(root, "note", "question", "--text", "Issue選択").returncode == 0
    rec = _history(root)[-1]
    assert rec["type"] == "intervention" and rec["kind"] == "question" and rec["text"] == "Issue選択"
    assert _vh(root, "note", "bogus").returncode != 0


def test_history_write_failure_does_not_break_run(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", boom)
    vh.append_history(tmp_path, {"type": "run", "at": "2026-10-24T10:00:00+09:00"})  # 例外を出さない
    assert "履歴を書き込めませんでした" in capsys.readouterr().err


def test_load_history_skips_corrupt_lines(tmp_path):
    p = tmp_path / ".claude" / "handoff"
    p.mkdir(parents=True)
    (p / "history.jsonl").write_text('{"type":"run","at":"2026-10-24T10:00:00+09:00"}\n{broken\n\n[1]\n', encoding="utf-8")
    assert len(vh.load_history(tmp_path)) == 1


def _run_rec(at, checks, partial=False, total=1.0):
    return {"type": "run", "at": at, "partial": partial, "total_seconds": total, "checks": checks}


def test_summarize_by_day_counts_runs_pass_rate_and_interventions():
    recs = [
        _run_rec("2026-10-23T10:00:00+09:00", [{"name": "t", "status": "fail", "seconds": 10}], total=10),
        _run_rec("2026-10-23T11:00:00+09:00", [{"name": "t", "status": "pass", "seconds": 20}], total=20),
        _run_rec("2026-10-24T09:00:00+09:00", [{"name": "t", "status": "pass", "seconds": 6}, {"name": "o", "status": "fail", "seconds": 1, "optional": True}],
                 partial=True, total=7),
        {"type": "intervention", "at": "2026-10-23T10:30:00+09:00", "kind": "question"},
        {"type": "intervention", "at": "2026-10-23T10:40:00+09:00", "kind": "question"},
        {"type": "intervention", "at": "2026-10-24T09:10:00+09:00", "kind": "redo"},
    ]
    s = vh.summarize(recs, "day")
    d23, d24 = s["2026-10-23"], s["2026-10-24"]
    assert (d23["runs"], d23["full_runs"], d23["failed_runs"]) == (2, 2, 1)
    assert d23["checks"]["t"] == {"runs": 2, "pass": 1, "pass_rate": 0.5, "avg_seconds": 15.0, "max_seconds": 20.0}
    assert d23["interventions"] == {"question": 2}
    # 部分実行は full_runs に数えず、optional の失敗は failed_runs に数えない
    assert (d24["runs"], d24["full_runs"], d24["failed_runs"]) == (1, 0, 0)
    assert d24["interventions"] == {"redo": 1}


def test_summarize_by_week_and_all():
    recs = [_run_rec("2026-10-19T10:00:00+09:00", [{"name": "t", "status": "pass", "seconds": 1}]),
            _run_rec("2026-10-26T10:00:00+09:00", [{"name": "t", "status": "pass", "seconds": 1}])]
    assert list(vh.summarize(recs, "week")) == ["2026-W43", "2026-W44"]
    assert vh.summarize(recs, "all")["all"]["runs"] == 2


def test_summarize_since_days_filters_old_records():
    now = datetime(2026, 10, 24, tzinfo=timezone(timedelta(hours=9)))
    recs = [_run_rec("2026-10-01T10:00:00+09:00", [{"name": "t", "status": "pass", "seconds": 1}]),
            _run_rec("2026-10-23T10:00:00+09:00", [{"name": "t", "status": "pass", "seconds": 1}])]
    assert vh.summarize(recs, "all", since_days=7, now=now)["all"]["runs"] == 1


def test_history_command_prints_summary_and_json(tmp_path):
    root = _repo(tmp_path, [{"name": "a", "cmd": "true"}])
    _vh(root, "run")
    _vh(root, "note", "approval")
    text = _vh(root, "history", "--by", "day").stdout
    assert "実行 1 回" in text and "approval=1" in text
    data = json.loads(_vh(root, "history", "--json").stdout)
    assert data["all"]["runs"] == 1 and data["all"]["interventions"] == {"approval": 1}


def test_history_command_on_repo_without_history(tmp_path):
    root = _repo(tmp_path, [{"name": "a", "cmd": "true"}])
    assert "履歴がありません" in _vh(root, "history").stdout


def test_run_that_matches_no_check_is_not_recorded(tmp_path):
    """--only の名前違い等で実行したチェックが0件の run は、検証ではないので履歴に残さない。"""
    root = _repo(tmp_path, [{"name": "a", "cmd": "true"}])
    _vh(root, "run", "--only", "typo")
    assert not (root / ".claude/handoff/history.jsonl").exists()
