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
        一時ファイル + os.replace で、読み手は常に旧か新の完全な内容を見ること。

        Issue #661: 実装は core/state_file.write_json_atomic へ移したため、
        json.dump の差し替え先も同モジュールになる(この不変条件自体は変わらない)。"""
        import json

        from core import state_file

        spm._save_persisted_states({"dev1": {"state": "on"}})

        observed = {}
        real_dump = json.dump

        def dump_and_peek(obj, fp, *a, **k):
            # 書き込みの真っ最中に本番パスを読む(=他プロセスの読み取りを模す)
            with open(isolated_state_file, "r", encoding="utf-8") as f:
                observed["during_write"] = f.read()
            return real_dump(obj, fp, *a, **k)

        monkeypatch.setattr(state_file.json, "dump", dump_and_peek)
        spm._save_persisted_states({"dev1": {"state": "off"}})

        assert json.loads(observed["during_write"]) == {"dev1": {"state": "on"}}
        assert spm._load_persisted_states() == {"dev1": {"state": "off"}}
        assert not [p for p in os.listdir(os.path.dirname(isolated_state_file)) if ".tmp." in p]


# ---------------------------------------------------------------------------
# Issue #758 (AUDIT-029): fetch_device_status_sync の API レスポンス耐性と、
# log_device_state_change の Silence Policy 分岐。
#
# 5分ごとに常駐的に回る収集スクリプトで、API が想定外の形を返したときに
# 例外で落ちる/無警告で欠損するとデータが静かに途切れる。
# ---------------------------------------------------------------------------
from unittest.mock import MagicMock


def _patch_status(monkeypatch, payload):
    monkeypatch.setattr(spm.sb_tool, "get_device_status", lambda device_id: payload)


class TestFetchDeviceStatusSync:
    def test_returns_none_when_api_returns_nothing(self, monkeypatch):
        _patch_status(monkeypatch, None)
        assert spm.fetch_device_status_sync("dev1", "Plug") is None

    def test_returns_none_on_api_error_status_code(self, monkeypatch):
        _patch_status(monkeypatch, {"statusCode": 190, "message": "device offline"})
        assert spm.fetch_device_status_sync("dev1", "Plug") is None

    def test_extracts_watt_as_power(self, monkeypatch):
        _patch_status(monkeypatch, {"statusCode": 100, "body": {"watt": 123.5}})
        assert spm.fetch_device_status_sync("dev1", "Plug") == {"power": 123.5}

    def test_falls_back_to_weight_then_power_for_the_analog_value(self, monkeypatch):
        """watt が無いモデルでは weight / power の順に拾う。"""
        _patch_status(monkeypatch, {"statusCode": 100, "body": {"weight": "42"}})
        assert spm.fetch_device_status_sync("dev1", "Plug")["power"] == 42.0

    def test_non_numeric_analog_value_is_skipped_without_raising(self, monkeypatch):
        """API が "--" 等を返しても例外にせず、その項目だけ落とす。"""
        _patch_status(monkeypatch, {"statusCode": 100, "body": {"watt": "--", "weight": None}})
        assert spm.fetch_device_status_sync("dev1", "Plug") == {}

    def test_negative_analog_value_is_ignored(self, monkeypatch):
        _patch_status(monkeypatch, {"statusCode": 100, "body": {"watt": -5}})
        assert spm.fetch_device_status_sync("dev1", "Plug") == {}

    def test_temperature_and_humidity_are_extracted(self, monkeypatch):
        _patch_status(
            monkeypatch, {"statusCode": 100, "body": {"temperature": "24.5", "humidity": "60"}}
        )
        assert spm.fetch_device_status_sync("dev1", "Meter") == {
            "temperature": 24.5, "humidity": 60.0
        }

    def test_unparsable_temperature_drops_both_values(self, monkeypatch):
        _patch_status(
            monkeypatch, {"statusCode": 100, "body": {"temperature": "n/a", "humidity": 60}}
        )
        assert spm.fetch_device_status_sync("dev1", "Meter") == {}

    def test_missing_humidity_defaults_to_zero(self, monkeypatch):
        _patch_status(monkeypatch, {"statusCode": 100, "body": {"temperature": 20}})
        assert spm.fetch_device_status_sync("dev1", "Meter") == {
            "temperature": 20.0, "humidity": 0.0
        }

    def test_string_power_is_read_as_a_digital_state(self, monkeypatch):
        """'power' が数値ではなく "on"/"off" の文字列で返るモデルへの対応。"""
        _patch_status(monkeypatch, {"statusCode": 100, "body": {"power": "on"}})
        assert spm.fetch_device_status_sync("dev1", "Plug") == {"power_state": "ON"}

    def test_power_state_field_is_normalised_to_upper_case(self, monkeypatch):
        _patch_status(monkeypatch, {"statusCode": 100, "body": {"powerState": "off"}})
        assert spm.fetch_device_status_sync("dev1", "Strip") == {"power_state": "OFF"}

    def test_body_missing_entirely_is_tolerated(self, monkeypatch):
        _patch_status(monkeypatch, {"statusCode": 100})
        assert spm.fetch_device_status_sync("dev1", "Plug") == {}

    def test_exception_from_the_api_client_returns_none(self, monkeypatch):
        def _boom(device_id):
            raise RuntimeError("connection reset by peer")

        monkeypatch.setattr(spm.sb_tool, "get_device_status", _boom)
        assert spm.fetch_device_status_sync("dev1", "Plug") is None


class TestLogDeviceStateChange:
    """Silence Policy 6.1: デジタルな状態変化だけを INFO、アナログの微変動は DEBUG。"""

    @pytest.fixture
    def fake_logger(self, monkeypatch):
        logger = MagicMock()
        monkeypatch.setattr(spm, "logger", logger)
        return logger

    def test_unchanged_state_is_debug(self, fake_logger):
        spm.log_device_state_change("plug", "d1", {"power": 1.0}, {"power": 1.0})
        fake_logger.info.assert_not_called()
        fake_logger.debug.assert_called_once()

    def test_initial_state_is_debug_not_info(self, fake_logger):
        """起動直後のログフラッドを防ぐため、初回取得は INFO に上げない。"""
        spm.log_device_state_change("plug", "d1", None, {"power_state": "ON"})
        fake_logger.info.assert_not_called()

    def test_digital_change_is_info(self, fake_logger):
        spm.log_device_state_change("plug", "d1", {"power_state": "ON"}, {"power_state": "OFF"})
        fake_logger.info.assert_called_once()

    def test_analog_only_change_is_debug(self, fake_logger):
        spm.log_device_state_change("meter", "d1", {"temperature": 24.8}, {"temperature": 24.9})
        fake_logger.info.assert_not_called()
        fake_logger.debug.assert_called_once()
