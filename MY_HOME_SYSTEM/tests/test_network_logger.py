# MY_HOME_SYSTEM/tests/test_network_logger.py
"""
monitors/network_logger.py の回帰テスト(Issue #190)。

1. pingレイテンシがサブプロセス起動時間込みの壁時計計測だったため、実RTTより
   系統的に大きい値が記録されていた。pingコマンド自身が報告する実測RTT
   (`time=X ms`)をパースして使うよう修正した。
2. logs/network_stats.csv へ無期限に追記され続け、ローテーション・削除経路が
   存在しなかった。deploy/logrotate/home_system にcopytruncate方式で追加した
   が、copytruncateはファイルを0バイトに切り詰めるだけで削除しないため、
   init_csv()が「ファイルが存在するが空」のケースでもヘッダーを再作成する
   よう修正した。
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors import network_logger


class TestParsePingLatency:
    def test_extracts_real_rtt_from_typical_ping_output(self):
        stdout = (
            "PING 192.168.1.1 (192.168.1.1) 56(84) bytes of data.\n"
            "64 bytes from 192.168.1.1: icmp_seq=1 ttl=64 time=0.055 ms\n"
            "\n"
            "--- 192.168.1.1 ping statistics ---\n"
            "1 packets transmitted, 1 received, 0% packet loss, time 0ms\n"
            "rtt min/avg/max/mdev = 0.055/0.055/0.055/0.000 ms\n"
        )
        assert network_logger._parse_ping_latency_ms(stdout) == 0.055

    def test_returns_none_for_unparseable_output(self):
        assert network_logger._parse_ping_latency_ms("garbage output with no timing info") is None
        assert network_logger._parse_ping_latency_ms("") is None


class TestPingHostUsesParsedRttNotWallClock:
    @pytest.mark.asyncio
    async def test_reports_pings_own_rtt_not_subprocess_startup_overhead(self, monkeypatch):
        """Issue #190の回帰テスト: 以前はサブプロセス起動〜終了までの壁時計時間
        (プロセス生成オーバーヘッド込み)をそのままlatencyとして記録していた。
        pingコマンド自身が報告する実測RTT(この例では0.055ms)を使うべきで、
        テスト側で意図的に大きい壁時計遅延をシミュレートしても、その値に
        引きずられないことを確認する。"""
        fake_process = MagicMock()
        fake_process.returncode = 0

        async def _slow_communicate():
            # サブプロセット起動オーバーヘッドを模した人為的な遅延
            # (実プロセスの生成コストをシミュレート)
            import asyncio as _asyncio
            await _asyncio.sleep(0.05)  # 50ms相当の「起動オーバーヘッド」
            stdout = b"64 bytes from 192.168.1.1: icmp_seq=1 ttl=64 time=0.055 ms\n"
            return stdout, b""

        fake_process.communicate = _slow_communicate

        async def _fake_create_subprocess_exec(*args, **kwargs):
            return fake_process

        monkeypatch.setattr(
            network_logger.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec
        )

        result = await network_logger.ping_host("192.168.1.1")

        assert result["status"] == "OK"
        # 壁時計時間(50ms超)ではなく、pingが報告した実測RTT(0.055ms、
        # round()による丸めで0.06)が使われること
        assert result["latency"] == round(0.055, 2)
        assert result["latency"] < 1.0, (
            f"latency={result['latency']}ms はサブプロセス起動の人為的遅延(50ms)に"
            "引きずられている可能性がある(=壁時計時間ベースの旧実装に戻っている)"
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_wall_clock_when_rtt_unparseable(self, monkeypatch):
        """pingの出力形式が想定外でRTTをパースできない場合は、例外にせず
        壁時計時間へフォールバックすること(可用性を優先)。"""
        fake_process = MagicMock()
        fake_process.returncode = 0
        fake_process.communicate = AsyncMock(return_value=(b"unparseable output", b""))

        async def _fake_create_subprocess_exec(*args, **kwargs):
            return fake_process

        monkeypatch.setattr(
            network_logger.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec
        )

        result = await network_logger.ping_host("192.168.1.1")

        assert result["status"] == "OK"
        assert result["latency"] >= 0.0


class TestInitCsvSurvivesLogrotateCopytruncate:
    def test_rewrites_header_when_file_exists_but_is_empty(self, tmp_path, monkeypatch):
        """Issue #190の回帰テスト: logrotateのcopytruncateはファイルを削除せず
        0バイトに切り詰める。以前のinit_csv()は「ファイルが存在しない場合のみ」
        ヘッダーを書いていたため、ローテーション後はヘッダー無しのままデータ行
        だけが追記され続けていた。"""
        csv_path = tmp_path / "network_stats.csv"
        csv_path.write_bytes(b"")  # copytruncate直後を模した0バイトファイル
        monkeypatch.setattr(network_logger, "CSV_FILE", str(csv_path))

        network_logger.init_csv()

        content = csv_path.read_text(encoding="utf-8")
        assert content.strip().split(",") == network_logger.CSV_HEADERS

    def test_does_not_touch_existing_nonempty_file(self, tmp_path, monkeypatch):
        """既に中身がある(ヘッダー+データ行が書かれた)ファイルは上書きしないこと。"""
        csv_path = tmp_path / "network_stats.csv"
        existing_content = "Timestamp,Camera_Name\n2026-01-01,cam1\n"
        csv_path.write_text(existing_content, encoding="utf-8")
        monkeypatch.setattr(network_logger, "CSV_FILE", str(csv_path))

        network_logger.init_csv()

        assert csv_path.read_text(encoding="utf-8") == existing_content

    def test_creates_file_with_header_when_missing(self, tmp_path, monkeypatch):
        csv_path = tmp_path / "subdir" / "network_stats.csv"
        monkeypatch.setattr(network_logger, "CSV_FILE", str(csv_path))

        network_logger.init_csv()

        assert csv_path.exists()
        content = csv_path.read_text(encoding="utf-8")
        assert content.strip().split(",") == network_logger.CSV_HEADERS


# ---------------------------------------------------------------------------
# ping / TCP / カメラ監視の本体。
#
# network_logger は `Restart=always` の常駐サービスで、結果は CSV に落ちるだけ。
# 判定を取り違えても誰も気づかないまま「カメラは正常」と記録され続けるため、
# 状態の出し分けを固定しておく(監査 AUDIT-029)。
# ---------------------------------------------------------------------------
import asyncio  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest  # noqa: E402

from monitors import network_logger  # noqa: E402


def _fake_ping_process(returncode, stdout=b""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


class TestPingHost:
    async def test_uses_rtt_reported_by_ping(self, monkeypatch):
        """#190: 壁時計ではなく ping 自身の time=X ms を使う。"""
        stdout = b"64 bytes from 192.168.1.10: icmp_seq=1 ttl=64 time=3.21 ms\n"
        monkeypatch.setattr(
            network_logger.asyncio,
            "create_subprocess_exec",
            AsyncMock(return_value=_fake_ping_process(0, stdout)),
        )

        result = await network_logger.ping_host("192.168.1.10")

        assert result["status"] == "OK"
        assert result["latency"] == 3.21
        assert result["error"] == ""

    async def test_falls_back_to_wall_clock_when_rtt_is_unparsable(self, monkeypatch):
        monkeypatch.setattr(
            network_logger.asyncio,
            "create_subprocess_exec",
            AsyncMock(return_value=_fake_ping_process(0, "pong (形式が変わった)".encode("utf-8"))),
        )

        result = await network_logger.ping_host("192.168.1.10")

        assert result["status"] == "OK"
        # 壁時計へのフォールバック。値は環境依存なので「正の数であること」だけ見る。
        assert result["latency"] > 0

    async def test_non_zero_exit_is_unreachable(self, monkeypatch):
        monkeypatch.setattr(
            network_logger.asyncio,
            "create_subprocess_exec",
            AsyncMock(return_value=_fake_ping_process(1)),
        )

        result = await network_logger.ping_host("192.168.1.99")

        assert result == {"status": "NG", "latency": 0.0, "error": "Unreachable"}

    async def test_subprocess_failure_is_reported_as_error(self, monkeypatch):
        monkeypatch.setattr(
            network_logger.asyncio,
            "create_subprocess_exec",
            AsyncMock(side_effect=OSError("ping not found")),
        )

        result = await network_logger.ping_host("192.168.1.10")

        assert result["status"] == "ERROR"
        assert "ping not found" in result["error"]


class TestCheckTcpPort:
    async def test_open_port_returns_latency(self, monkeypatch):
        writer = MagicMock()
        writer.wait_closed = AsyncMock()
        monkeypatch.setattr(
            network_logger.asyncio, "open_connection", AsyncMock(return_value=(MagicMock(), writer))
        )

        result = await network_logger.check_tcp_port("192.168.1.10", 554)

        assert result["status"] == "OPEN"
        assert result["latency"] >= 0
        # 接続は必ず閉じる(常駐プロセスなので FD が溜まると最終的に開けなくなる)
        writer.close.assert_called_once()

    @pytest.mark.parametrize(
        "error, expected",
        [
            (asyncio.TimeoutError(), "TIMEOUT"),
            (ConnectionRefusedError(), "REFUSED"),
            (OSError("no route to host"), "ERROR"),
        ],
    )
    async def test_failure_modes_are_distinguished(self, monkeypatch, error, expected):
        """TIMEOUT と REFUSED は原因が違うので、CSV 上でも区別できる必要がある。"""
        monkeypatch.setattr(
            network_logger.asyncio, "open_connection", AsyncMock(side_effect=error)
        )

        result = await network_logger.check_tcp_port("192.168.1.10", 554)

        assert result == {"status": expected, "latency": 0.0}


class TestMonitorCamera:
    async def test_skips_config_without_ip(self):
        assert await network_logger.monitor_camera({"name": "壊れた設定"}) is None

    async def test_healthy_camera_has_no_error_detail(self, monkeypatch):
        monkeypatch.setattr(
            network_logger, "ping_host", AsyncMock(return_value={"status": "OK", "latency": 2.0, "error": ""})
        )
        monkeypatch.setattr(
            network_logger, "check_tcp_port", AsyncMock(return_value={"status": "OPEN", "latency": 1.5})
        )

        row = await network_logger.monitor_camera({"name": "玄関", "ip": "192.168.1.10"})

        assert row["Camera_Name"] == "玄関"
        assert row["Ping_Status"] == "OK"
        assert row["Port_RTSP_Status"] == "OPEN"
        assert row["Error_Detail"] == ""

    async def test_retries_ping_then_skips_rtsp_when_unreachable(self, monkeypatch):
        ping = AsyncMock(return_value={"status": "NG", "latency": 0.0, "error": "Unreachable"})
        tcp = AsyncMock()
        monkeypatch.setattr(network_logger, "ping_host", ping)
        monkeypatch.setattr(network_logger, "check_tcp_port", tcp)
        monkeypatch.setattr(network_logger.asyncio, "sleep", AsyncMock())

        row = await network_logger.monitor_camera({"name": "庭", "ip": "192.168.1.11"})

        assert ping.await_count == network_logger.PING_RETRY_COUNT
        # ping が通らない相手に RTSP を試しても意味がないので呼ばない
        tcp.assert_not_awaited()
        assert "Ping:Unreachable" in row["Error_Detail"]
        assert "RTSP:Skipped" in row["Error_Detail"]

    async def test_stops_retrying_once_ping_succeeds(self, monkeypatch):
        ping = AsyncMock(
            side_effect=[
                {"status": "NG", "latency": 0.0, "error": "Unreachable"},
                {"status": "OK", "latency": 4.0, "error": ""},
            ]
        )
        monkeypatch.setattr(network_logger, "ping_host", ping)
        monkeypatch.setattr(
            network_logger, "check_tcp_port", AsyncMock(return_value={"status": "OPEN", "latency": 1.0})
        )
        monkeypatch.setattr(network_logger.asyncio, "sleep", AsyncMock())

        row = await network_logger.monitor_camera({"name": "駐車場", "ip": "192.168.1.12"})

        assert ping.await_count == 2
        assert row["Error_Detail"] == ""

    async def test_reachable_but_rtsp_closed_is_recorded(self, monkeypatch):
        """録画が止まる典型パターン(機器は生きているが RTSP だけ落ちている)。"""
        monkeypatch.setattr(
            network_logger, "ping_host", AsyncMock(return_value={"status": "OK", "latency": 2.0, "error": ""})
        )
        monkeypatch.setattr(
            network_logger, "check_tcp_port", AsyncMock(return_value={"status": "REFUSED", "latency": 0.0})
        )

        row = await network_logger.monitor_camera({"name": "玄関", "ip": "192.168.1.10"})

        assert row["Ping_Status"] == "OK"
        assert row["Error_Detail"] == "RTSP:REFUSED"

    async def test_falls_back_to_unknown_camera_name(self, monkeypatch):
        monkeypatch.setattr(
            network_logger, "ping_host", AsyncMock(return_value={"status": "OK", "latency": 1.0, "error": ""})
        )
        monkeypatch.setattr(
            network_logger, "check_tcp_port", AsyncMock(return_value={"status": "OPEN", "latency": 1.0})
        )

        row = await network_logger.monitor_camera({"ip": "192.168.1.13"})

        assert row["Camera_Name"] == "Unknown_Camera"
