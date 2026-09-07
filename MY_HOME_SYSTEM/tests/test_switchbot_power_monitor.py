# MY_HOME_SYSTEM/tests/test_switchbot_power_monitor.py
"""
monitors/switchbot_power_monitor.py の状態変化検知(_last_device_states)のテスト。

M-4-5: _last_device_states はプロセス内メモリのみのキャッシュだったが、
scheduler_boot.py はこのスクリプトを5分ごとに subprocess.run(...) で
**毎回新しいプロセスとして**起動する(run_script参照)。そのため
_last_device_states は実行のたびに空の辞書から始まり、log_device_state_change()
は常に「初回取得(last_status is None)」として扱ってしまい、ON/OFF等の
デジタル状態変化が INFO ログとして一度も記録されない構造的なバグがあった。

このテストでは、モジュールレベルの `_last_device_states` を明示的に clear()
することで「scheduler_boot.py が新規プロセスを起動した直後」の状態を再現し、
ディスクへの永続化ファイルから前回状態が正しく復元されることを検証する。
"""
import os
import sys

import pytest
from unittest.mock import patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from monitors import switchbot_power_monitor as spm


@pytest.fixture
def isolated_state_file(tmp_path, monkeypatch):
    state_path = str(tmp_path / "switchbot_device_states.json")
    monkeypatch.setattr(spm, "_STATE_FILE", state_path)
    spm._last_device_states.clear()
    yield state_path
    spm._last_device_states.clear()


async def _run_main_with_status(monkeypatch, device, status):
    monkeypatch.setattr(config, "MONITOR_DEVICES", [device], raising=False)

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(spm.sensor_service, "process_power_data", _noop)
    monkeypatch.setattr(spm.sensor_service, "process_meter_data", _noop)

    # C-L2 (Issue #414): main() 末尾の asyncio.sleep(2) を実時間で待たない
    async def _no_sleep(*args, **kwargs):
        return None

    with patch.object(spm, "fetch_device_status_sync", return_value=status), \
         patch.object(spm.asyncio, "sleep", _no_sleep):
        await spm.main()


class TestDeviceStatePersistsAcrossProcessRestarts:
    async def test_state_survives_a_simulated_process_restart(self, isolated_state_file, monkeypatch):
        device = {"id": "dev1", "name": "TestPlug", "type": "Plug Mini (JP)"}

        await _run_main_with_status(monkeypatch, device, {"power_state": "OFF"})
        assert os.path.exists(isolated_state_file), "device state should be persisted to disk after main()"

        # scheduler_boot.py の subprocess.run による「毎回新規プロセス」を再現するため、
        # プロセス内メモリキャッシュだけを明示的にクリアする(ディスクの永続化ファイルは残る)。
        spm._last_device_states.clear()

        await _run_main_with_status(monkeypatch, device, {"power_state": "ON"})

        # ディスクから前回状態(OFF)が読み込まれ、正しく最新値(ON)へ更新されていること。
        assert spm._last_device_states["dev1"] == {"power_state": "ON"}

    async def test_on_off_change_is_detected_as_digital_change_not_initial_state(
        self, isolated_state_file, monkeypatch
    ):
        device = {"id": "dev1", "name": "TestPlug", "type": "Plug Mini (JP)"}

        await _run_main_with_status(monkeypatch, device, {"power_state": "OFF"})
        spm._last_device_states.clear()  # 「新規プロセスでの2回目の定期実行」を再現

        with patch.object(spm, "log_device_state_change") as mock_log:
            await _run_main_with_status(monkeypatch, device, {"power_state": "ON"})

        # 修正前はプロセス再起動のたびに last_status=None (=初回取得扱い) になり、
        # ON/OFFの切り替わりが二度と「デジタル状態変化」として検知されなかった。
        mock_log.assert_called_once()
        _dname, _did, last_status_arg, current_status_arg = mock_log.call_args[0]
        assert last_status_arg == {"power_state": "OFF"}, (
            "last_status should be restored from the persisted state file, not None, "
            "even though this simulates a freshly-started process"
        )
        assert current_status_arg == {"power_state": "ON"}


class TestPersistedStateWriteIsAtomic:
    def test_reader_never_sees_truncated_file_during_write(self, isolated_state_file, monkeypatch):
        """open(..., "w") はロック取得より前にファイルを切り詰めるため、書き込み途中に
        読んだ側が空ファイル → JSONDecodeError → {} (全デバイス初期状態扱い)になっていた。
        一時ファイル + os.replace で、読み手は常に旧か新の完全な内容を見ること。"""
        import json
        spm._save_persisted_states({"dev1": {"state": "on"}})

        observed = {}
        real_dump = json.dump

        def dump_and_peek(obj, fp, *a, **k):
            # 書き込みの真っ最中に本番パスを読む(=他プロセスの読み取りを模す)
            with open(isolated_state_file, "r", encoding="utf-8") as f:
                observed["during_write"] = f.read()
            return real_dump(obj, fp, *a, **k)

        monkeypatch.setattr(spm.json, "dump", dump_and_peek)
        spm._save_persisted_states({"dev1": {"state": "off"}})

        assert json.loads(observed["during_write"]) == {"dev1": {"state": "on"}}
        assert spm._load_persisted_states() == {"dev1": {"state": "off"}}
        assert not [p for p in os.listdir(os.path.dirname(isolated_state_file)) if ".tmp." in p]
