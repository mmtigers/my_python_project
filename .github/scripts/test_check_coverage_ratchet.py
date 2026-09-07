# .github/scripts/test_check_coverage_ratchet.py
"""check_coverage_ratchet.py の回帰テスト。

Cobertura XML / vitest json-summary の読み取り、tolerance 判定、比較元が無い/壊れている
ときのスキップ挙動を検証する。CI では test.yml の lint ジョブから
`pytest .github/scripts/` で実行される。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent / "check_coverage_ratchet.py"
_spec = importlib.util.spec_from_file_location("check_coverage_ratchet", SCRIPT_PATH)
module = importlib.util.module_from_spec(_spec)
sys.modules["check_coverage_ratchet"] = module
_spec.loader.exec_module(module)  # type: ignore[union-attr]


def _cobertura(tmp_path: Path, name: str, line_rate: float) -> Path:
    p = tmp_path / name
    p.write_text(
        f'<?xml version="1.0" ?>\n<coverage version="7.0" line-rate="{line_rate}" '
        'branch-rate="0" lines-covered="1" lines-valid="1"><packages/></coverage>\n',
        encoding="utf-8",
    )
    return p


def _json_summary(tmp_path: Path, name: str, pct: float) -> Path:
    p = tmp_path / name
    p.write_text(
        '{"total": {"lines": {"total": 100, "covered": 50, "skipped": 0, "pct": %s}, '
        '"statements": {"pct": 1}}}' % pct,
        encoding="utf-8",
    )
    return p


class TestRead:
    def test_reads_cobertura_line_rate_as_percent(self, tmp_path):
        assert module.read_line_coverage_percent(_cobertura(tmp_path, "c.xml", 0.7386)) == pytest.approx(73.86)

    def test_reads_vitest_json_summary(self, tmp_path):
        assert module.read_line_coverage_percent(_json_summary(tmp_path, "s.json", 42.5)) == pytest.approx(42.5)

    def test_rejects_unknown_format(self, tmp_path):
        p = tmp_path / "cov.txt"
        p.write_text("x", encoding="utf-8")
        with pytest.raises(ValueError):
            module.read_line_coverage_percent(p)


class TestCompare:
    def test_within_tolerance_passes(self):
        code, msg = module.compare(73.6, 74.0, 0.5, "backend")
        assert code == 0 and "OK" in msg

    def test_drop_beyond_tolerance_fails(self):
        code, msg = module.compare(72.0, 74.0, 0.5, "backend")
        assert code == 1 and "::error::" in msg and "2.00pt" in msg

    def test_increase_passes(self):
        assert module.compare(80.0, 74.0, 0.5, "x")[0] == 0

    def test_missing_baseline_skips(self):
        code, msg = module.compare(10.0, None, 0.5, "x")
        assert code == 0 and "スキップ" in msg


class TestMain:
    def test_fails_when_pr_coverage_drops(self, tmp_path, capsys):
        cur = _cobertura(tmp_path, "cur.xml", 0.70)
        base = _cobertura(tmp_path, "base.xml", 0.74)
        assert module.main(["--current", str(cur), "--baseline", str(base)]) == 1

    def test_skips_when_baseline_file_missing(self, tmp_path, capsys):
        cur = _cobertura(tmp_path, "cur.xml", 0.10)
        rc = module.main(["--current", str(cur), "--baseline", str(tmp_path / "none.xml")])
        assert rc == 0
        assert "スキップ" in capsys.readouterr().out

    def test_skips_with_warning_when_baseline_is_broken(self, tmp_path, capsys):
        cur = _cobertura(tmp_path, "cur.xml", 0.10)
        broken = tmp_path / "base.xml"
        broken.write_text("<not-xml", encoding="utf-8")
        assert module.main(["--current", str(cur), "--baseline", str(broken)]) == 0
        assert "::warning::" in capsys.readouterr().out

    def test_errors_when_current_is_unreadable(self, tmp_path):
        assert module.main(["--current", str(tmp_path / "missing.xml"),
                            "--baseline", str(tmp_path / "none.xml")]) == 2

    def test_frontend_json_summary_with_custom_tolerance(self, tmp_path):
        cur = _json_summary(tmp_path, "cur.json", 40.0)
        base = _json_summary(tmp_path, "base.json", 41.0)
        assert module.main(["--current", str(cur), "--baseline", str(base), "--tolerance", "0.5"]) == 1
        assert module.main(["--current", str(cur), "--baseline", str(base), "--tolerance", "1.0"]) == 0
