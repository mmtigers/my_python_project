# MY_HOME_SYSTEM/tests/test_server_watchdog_hardware.py
"""
monitors/server_watchdog.py のハードウェア監視(スロットリング・電圧低下・CPU温度)のテスト。

2026-09 の OS 更新で userland だけが新しくなり vcgencmd が失敗し続けたが、以前はそれを
DEBUG ログで握りつぶしていたため、監視が止まったことに誰も気づけなかった。
vcgencmd が使えない時は hwmon で電圧低下を代替判定し、監視が弱っていることを
ブート毎に1回 ERROR で知らせること、温度はカーネルの thermal_zone から読むことを検証する。
"""
import logging
import os
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors import server_watchdog


class _RecordList(logging.Handler):
    """watchdog ロガーに直接付けて記録を集める。

    watchdog ロガーは propagate=False なので caplog には届かない。以前は propagate を
    True にして caplog で拾っていたが、pytest 9.1 から caplog.at_level(logger=...) が
    対象ロガーにもハンドラを付けるようになり、同じ記録が2回入った(CI だけ失敗)。
    pytest のバージョンに依存しないよう、自前のハンドラで受ける。
    """

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def clear(self):
        self.records.clear()

    def capturing(self):
        return nullcontext()


@pytest.fixture
def watchdog_log(monkeypatch):
    handler = _RecordList()
    monkeypatch.setattr(server_watchdog.logger, "level", logging.DEBUG)
    server_watchdog.logger.addHandler(handler)
    yield handler
    server_watchdog.logger.removeHandler(handler)


def _fake_hwmon(tmp_path: Path, alarm: str) -> Path:
    base = tmp_path / "hwmon"
    (base / "hwmon0").mkdir(parents=True)
    (base / "hwmon0" / "name").write_text("cpu_thermal\n")
    (base / "hwmon1").mkdir()
    (base / "hwmon1" / "name").write_text("rpi_volt\n")
    (base / "hwmon1" / "in0_lcrit_alarm").write_text(alarm + "\n")
    return base


def _setup(tmp_path, monkeypatch, alarm="0", boot_id="boot-a"):
    monkeypatch.setattr(server_watchdog, "HWMON_DIR", _fake_hwmon(tmp_path, alarm))
    monkeypatch.setattr(server_watchdog, "VCGENCMD_UNAVAILABLE_STATE_FILE", tmp_path / "vcgencmd.state")
    monkeypatch.setattr(server_watchdog, "_get_boot_id", lambda: boot_id)


def _errors(watchdog_log):
    return [r.getMessage() for r in watchdog_log.records if r.levelno >= logging.ERROR]


class TestThrottlingWithoutVcgencmd:
    def _run(self, monkeypatch, watchdog_log, returncode=255):
        failed = subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr="")
        with watchdog_log.capturing(), \
                patch.object(server_watchdog.subprocess, "run", return_value=failed):
            server_watchdog.check_throttling_status()

    def test_vcgencmd_failure_is_reported_once_per_boot(self, tmp_path, monkeypatch, watchdog_log):
        _setup(tmp_path, monkeypatch)
        self._run(monkeypatch, watchdog_log)
        errors = _errors(watchdog_log)
        assert len(errors) == 1 and "vcgencmd が使えない" in errors[0] and "hwmon で監視を継続" in errors[0]

        watchdog_log.clear()
        self._run(monkeypatch, watchdog_log)
        assert _errors(watchdog_log) == []

    def test_reported_again_after_reboot(self, tmp_path, monkeypatch, watchdog_log):
        _setup(tmp_path, monkeypatch, boot_id="boot-a")
        self._run(monkeypatch, watchdog_log)
        watchdog_log.clear()
        monkeypatch.setattr(server_watchdog, "_get_boot_id", lambda: "boot-b")
        self._run(monkeypatch, watchdog_log)
        assert len(_errors(watchdog_log)) == 1

    def test_active_undervoltage_from_hwmon_is_error_every_time(self, tmp_path, monkeypatch, watchdog_log):
        _setup(tmp_path, monkeypatch, alarm="1")
        self._run(monkeypatch, watchdog_log)
        watchdog_log.clear()
        self._run(monkeypatch, watchdog_log)
        errors = _errors(watchdog_log)
        assert errors == ["⚠️ System Alert: Under-voltage detected (hwmon rpi_volt in0_lcrit_alarm=1)"]

    def test_missing_vcgencmd_binary_also_falls_back(self, tmp_path, monkeypatch, watchdog_log):
        _setup(tmp_path, monkeypatch)
        with watchdog_log.capturing(), \
                patch.object(server_watchdog.subprocess, "run", side_effect=FileNotFoundError()):
            server_watchdog.check_throttling_status()
        errors = _errors(watchdog_log)
        assert len(errors) == 1 and "コマンドが見つかりません" in errors[0]

    def test_unreadable_hwmon_is_mentioned(self, tmp_path, monkeypatch, watchdog_log):
        _setup(tmp_path, monkeypatch)
        monkeypatch.setattr(server_watchdog, "HWMON_DIR", tmp_path / "no-hwmon")
        self._run(monkeypatch, watchdog_log)
        errors = _errors(watchdog_log)
        assert len(errors) == 1 and "電圧低下も読み取れません" in errors[0]


class TestCpuTemperature:
    def _setup(self, tmp_path, monkeypatch, millideg: str):
        temp_file = tmp_path / "temp"
        temp_file.write_text(millideg + "\n")
        monkeypatch.setattr(server_watchdog, "THERMAL_ZONE_TEMP_FILE", temp_file)
        monkeypatch.setattr(server_watchdog, "CPU_TEMP_ALERT_STATE_FILE", tmp_path / "temp.state")

    def test_reads_millidegrees(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, "61250")
        assert server_watchdog.read_cpu_temp_c() == 61.25

    def test_normal_temperature_is_silent(self, tmp_path, monkeypatch, watchdog_log):
        self._setup(tmp_path, monkeypatch, "61250")
        with watchdog_log.capturing():
            server_watchdog.check_cpu_temperature(now=1000.0)
        assert _errors(watchdog_log) == []

    def test_high_temperature_alerts_then_suppresses_then_realerts(self, tmp_path, monkeypatch, watchdog_log):
        self._setup(tmp_path, monkeypatch, "82000")
        with watchdog_log.capturing():
            server_watchdog.check_cpu_temperature(now=1000.0)
            assert len(_errors(watchdog_log)) == 1 and "82.0°C" in _errors(watchdog_log)[0]
            watchdog_log.clear()
            server_watchdog.check_cpu_temperature(now=1000.0 + 600)
            assert _errors(watchdog_log) == []
            server_watchdog.check_cpu_temperature(now=1000.0 + server_watchdog.CPU_TEMP_REALERT_SEC + 1)
            assert len(_errors(watchdog_log)) == 1

    def test_unreadable_temperature_is_warning_not_crash(self, tmp_path, monkeypatch, watchdog_log):
        self._setup(tmp_path, monkeypatch, "0")
        monkeypatch.setattr(server_watchdog, "THERMAL_ZONE_TEMP_FILE", tmp_path / "missing")
        with watchdog_log.capturing():
            server_watchdog.check_cpu_temperature(now=1000.0)
        assert _errors(watchdog_log) == []
        assert any("CPU温度を読み取れません" in r.getMessage() for r in watchdog_log.records)
