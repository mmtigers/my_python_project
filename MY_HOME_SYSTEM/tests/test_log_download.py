"""ログのダウンロード(services/log_download_service.py とダッシュボードのルート)のテスト。"""
import os

import config
import pytest
from services import log_download_service as lds


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    d = tmp_path / "logs"
    d.mkdir()
    monkeypatch.setattr(config, "LOG_DIR", str(d), raising=False)
    # DDDのログ置き場は存在しない場所に向ける(実リポジトリのDDD/logsを拾わない)
    monkeypatch.setattr(lds, "_ddd_log_dir", lambda: str(tmp_path / "no_ddd"))
    return d


def test_list_includes_only_log_files(log_dir):
    (log_dir / "home_system.log").write_text("a", encoding="utf-8")
    (log_dir / "home_system.log.1").write_text("b", encoding="utf-8")
    (log_dir / "home_system.log.2.gz").write_bytes(b"gz")
    (log_dir / "secret.env").write_text("X=1", encoding="utf-8")
    (log_dir / "network_stats.csv").write_text("x", encoding="utf-8")
    names = {f.name for f in lds.list_log_files()}
    assert names == {"home_system.log", "home_system.log.1"}


def test_symlink_is_excluded(log_dir, tmp_path):
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    os.symlink(target, log_dir / "evil.log")
    assert lds.list_log_files() == []


def test_ddd_log_is_listed_with_prefix(log_dir, tmp_path, monkeypatch):
    ddd = tmp_path / "ddd_logs"
    ddd.mkdir()
    (ddd / "newface_monitor.log").write_text("x", encoding="utf-8")
    monkeypatch.setattr(lds, "_ddd_log_dir", lambda: str(ddd))
    assert "DDD__newface_monitor.log" in {f.name for f in lds.list_log_files()}


@pytest.mark.parametrize("name", ["../etc/passwd", "..%2f..%2fetc%2fpasswd", "/etc/passwd", "nope.log", "home_system.log/../x"])
def test_resolve_rejects_unknown_or_traversal(log_dir, name):
    (log_dir / "home_system.log").write_text("a", encoding="utf-8")
    assert lds.resolve_log_file(name) is None


def test_redact_secrets():
    text = (
        "POST /webhook/switchbot?token=abc123SECRET 200\n"
        'payload {"password": "hunter2", "api_key": "KEY999"}\n'
        "Authorization: Bearer abcdefghijklmnop\n"
        "rtsp://admin:pa55w0rd@192.168.0.10:554/stream\n"
        "https://discord.com/api/webhooks/123456/TOKENTOKEN failed\n"
        "ordinary line with no secret\n"
    )
    out = lds.redact_secrets(text)
    for leaked in ("abc123SECRET", "hunter2", "KEY999", "abcdefghijklmnop", "pa55w0rd", "TOKENTOKEN"):
        assert leaked not in out
    assert "ordinary line with no secret" in out
    assert "192.168.0.10" in out  # IPは伏せない(仕様)


def test_read_truncates_to_tail(log_dir, monkeypatch):
    monkeypatch.setattr(config, "LOG_DOWNLOAD_MAX_MB", 1)
    lines = [f"2026-10-24 line {i:06d}\n" for i in range(100_000)]  # 約2.7MB
    (log_dir / "big.log").write_text("".join(lines), encoding="utf-8")
    text, truncated = lds.read_log_for_download(lds.resolve_log_file("big.log"))
    assert truncated is True
    assert text.startswith("# [log_download]")
    body = text.split("\n", 1)[1]
    assert body.endswith("line 099999\n")
    assert body.startswith("2026-10-24 line ")  # 欠けた先頭行は捨てている
    assert len(text.encode("utf-8")) < 1024 * 1024 + 400


def test_read_small_file_not_truncated_and_empty_ok(log_dir):
    (log_dir / "empty.log").write_text("", encoding="utf-8")
    text, truncated = lds.read_log_for_download(lds.resolve_log_file("empty.log"))
    assert (text, truncated) == ("", False)


def test_download_endpoint_ok_and_headers(api_client, log_dir):
    (log_dir / "home_system.log").write_text("hello token=SECRETVAL\n", encoding="utf-8")
    r = api_client.get(f"{config.DASHBOARD_BASE_PATH}/sys/logs/home_system.log")
    assert r.status_code == 200
    assert "SECRETVAL" not in r.text and "hello" in r.text
    assert r.headers["content-disposition"].startswith('attachment; filename="home_system.log.')
    assert r.headers["content-disposition"].endswith('.txt"')
    assert r.headers["content-type"].startswith("text/plain")


@pytest.mark.parametrize("name", ["missing.log", "..%2f..%2fetc%2fpasswd", "secret.env"])
def test_download_endpoint_404(api_client, log_dir, name):
    (log_dir / "secret.env").write_text("X=1", encoding="utf-8")
    assert api_client.get(f"{config.DASHBOARD_BASE_PATH}/sys/logs/{name}").status_code == 404


def test_sys_page_lists_logs(api_client, log_dir):
    (log_dir / "home_system.log").write_text("x", encoding="utf-8")
    r = api_client.get(f"{config.DASHBOARD_BASE_PATH}/sys")
    assert r.status_code == 200
    assert 'id="log-download"' in r.text
    assert f"{config.DASHBOARD_BASE_PATH}/sys/logs/home_system.log" in r.text


def test_sys_page_with_no_logs(api_client, log_dir):
    r = api_client.get(f"{config.DASHBOARD_BASE_PATH}/sys")
    assert r.status_code == 200
    assert "ダウンロードできるログはありません" in r.text
