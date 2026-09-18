# MY_HOME_SYSTEM/tests/test_personal_defaults.py
"""
Issue #663: 個人環境値(実機の LAN IP)を既定値から外したことの回帰テスト。

個人データがリポジトリに残らないことに加えて、**外したことで挙動が悪化して
いない**ことを固定する。特に `NAS_IP` 未設定時の `check_ping()` は、
`NasMonitor.run()` で `check_mount()`/`check_write_permission()` を実行するか
どうかのゲートになっているため、ここで False を返すと「アドレスを知らない」
だけで NAS 障害と判定され、通知を飛ばしてローカルフォールバックへ退避して
しまう(= 本番データの保存先が勝手に切り替わる)。
"""
import os
import re
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from monitors.nas_monitor import NasMonitor

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# RFC1918 のうち、このリポジトリが実環境として使っていた 192.168.1.0/24 の
# ホストアドレス。プレースホルダーとして紛れ込ませないための検出パターン。
_DEPLOYMENT_SUBNET = re.compile(r"192\.168\.1\.\d{1,3}")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestNoPersonalAddressesInDefaults:
    """config.py の既定値に実環境のアドレスが残っていないこと。"""

    def test_nas_ip_defaults_to_empty(self):
        """NAS_IP の既定は空文字(実機の IP ではない)。"""
        assert config.NAS_IP == os.getenv("NAS_IP", "")

    def test_frontend_url_defaults_to_loopback(self):
        """FRONTEND_URL の既定はループバック(LAN の他端末のアドレスではない)。"""
        if os.getenv("FRONTEND_URL"):
            return  # .env で明示されている環境では既定値を検証できない
        assert config.FRONTEND_URL == "http://127.0.0.1:8000/quest"

    def test_frontend_origin_stays_a_valid_origin(self):
        """
        既定値でも CORS に入れる origin が scheme://host[:port] の形を保つこと。
        空文字を既定にすると urlparse の結果が "://" という壊れた origin になる。
        """
        origins = [o for o in config.CORS_ORIGINS if o != "*"]
        for origin in origins:
            assert re.match(r"^https?://[^/]+$", origin), f"壊れた origin: {origin!r}"

    def test_config_source_has_no_deployment_subnet_literal(self):
        """
        config.py 本体に 192.168.1.x のリテラルが残っていないこと。

        コメント内の例示も許さない(.env.example 冒頭の「実際の個人データを
        含めない」方針を config.py にも適用する。Issue #663)。
        """
        source = _read(os.path.join(BASE_DIR, "config.py"))
        assert _DEPLOYMENT_SUBNET.search(source) is None

    def test_env_example_has_no_deployment_subnet_literal(self):
        """.env.example も同様(このファイル自身が冒頭でそう宣言している)。"""
        source = _read(os.path.join(BASE_DIR, ".env.example"))
        assert _DEPLOYMENT_SUBNET.search(source) is None

    def test_no_duplicated_default_ip_in_callers(self):
        """
        呼び出し側が getattr のフォールバックとして IP を重複して持たないこと。

        以前は post_boot_health_check.py と nas_monitor.py が config.py と同じ
        既定値を各自に書いており、config.py だけ直しても実質変わらなかった。
        """
        for rel in ("post_boot_health_check.py", "monitors/nas_monitor.py"):
            source = _read(os.path.join(BASE_DIR, rel))
            assert _DEPLOYMENT_SUBNET.search(source) is None, f"{rel} に既定IPが残っている"


class TestNasMonitorWithUnsetIp:
    """NAS_IP 未設定時に NasMonitor が誤検知しないこと。"""

    def test_check_ping_is_skipped_and_reports_reachable(self, monkeypatch):
        """
        ping をスキップし、True を返す。

        False を返すと run() が mount/write を一切試さないまま「NAS障害」と
        判定し、通知 + フォールバック退避まで進んでしまう。
        """
        monkeypatch.setattr(config, "NAS_IP", "", raising=False)
        monitor = NasMonitor()

        def _fail_if_called(*args, **kwargs):
            raise AssertionError("NAS_IP 未設定なのに ping を実行した")

        monkeypatch.setattr("monitors.nas_monitor.subprocess.run", _fail_if_called)

        assert monitor.check_ping() is True

    def test_check_ping_still_pings_when_ip_is_set(self, monkeypatch):
        """設定されていれば従来どおり ping する(スキップが常時有効にならない)。"""
        monkeypatch.setattr(config, "NAS_IP", "203.0.113.9", raising=False)
        monitor = NasMonitor()

        calls = []

        class _Result:
            returncode = 0

        def _fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _Result()

        monkeypatch.setattr("monitors.nas_monitor.subprocess.run", _fake_run)

        assert monitor.check_ping() is True
        assert calls and calls[0][-1] == "203.0.113.9"

    def test_unreachable_ip_is_still_reported_as_down(self, monkeypatch):
        """到達不可は従来どおり False(スキップと取り違えていないこと)。"""
        monkeypatch.setattr(config, "NAS_IP", "203.0.113.9", raising=False)
        monitor = NasMonitor()

        class _Result:
            returncode = 1

        monkeypatch.setattr("monitors.nas_monitor.subprocess.run", lambda cmd, **kw: _Result())

        assert monitor.check_ping() is False
