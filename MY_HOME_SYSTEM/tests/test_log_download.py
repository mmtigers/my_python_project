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


def _file(name):
    return next(f for f in lds.list_log_files() if f.name == name)


def test_redact_secrets():
    text = (
        "POST /webhook/switchbot?token=abc123SECRET 200\n"
        'payload {"password": "hunter2", "api_key": "KEY999"}\n'
        "Authorization: Bearer abcdefghijklmnop\n"
        "rtsp://admin:pa55w0rd@192.168.0.10:554/stream\n"
        "https://discord.com/api/webhooks/123456/TOKENTOKEN failed\n"
        "LINE_CHANNEL_ACCESS_TOKEN=LINETOK123\n"
        'cfg {"channel_secret": "CHSEC456", "client_secret": "CLSEC789"}\n'
        "csrf_token: CSRF000\n"
        "ordinary line with no secret\n"
    )
    out = lds.redact_secrets(text)
    for leaked in ("abc123SECRET", "hunter2", "KEY999", "abcdefghijklmnop", "pa55w0rd", "TOKENTOKEN",
                   "LINETOK123", "CHSEC456", "CLSEC789", "CSRF000"):
        assert leaked not in out
    assert "ordinary line with no secret" in out
    assert "192.168.0.10" in out  # IPは伏せない(仕様)


def test_redact_secrets_is_linear_on_long_unbroken_strings():
    # 区切りの無い長い文字列(base64等)でも処理時間が二乗に増えない
    import time

    for blob in ("x" * 700_000, "ab-cd_" * 120_000):
        started = time.monotonic()
        assert lds.redact_secrets(blob) == blob
        assert time.monotonic() - started < 5


def test_read_truncates_to_tail(log_dir, monkeypatch):
    monkeypatch.setattr(config, "LOG_DOWNLOAD_MAX_MB", 1)
    lines = [f"2026-10-24 line {i:06d}\n" for i in range(100_000)]  # 約2.7MB
    (log_dir / "big.log").write_text("".join(lines), encoding="utf-8")
    text, truncated = lds.read_log_for_download(_file("big.log"))
    assert truncated is True
    assert text.startswith("# [log_download]")
    body = text.split("\n", 1)[1]
    assert body.endswith("line 099999\n")
    assert body.startswith("2026-10-24 line ")  # 欠けた先頭行は捨てている
    assert len(text.encode("utf-8")) < 1024 * 1024 + 400


def test_read_small_file_not_truncated_and_empty_ok(log_dir):
    (log_dir / "empty.log").write_text("", encoding="utf-8")
    text, truncated = lds.read_log_for_download(_file("empty.log"))
    assert (text, truncated) == ("", False)


def _url(*groups):
    qs = "&".join(f"group={g}" for g in groups)
    return f"{config.DASHBOARD_BASE_PATH}/sys/logs?{qs}"


def test_groups_merge_rotated_file_oldest_first(log_dir):
    (log_dir / "home_system.log").write_text("new\n", encoding="utf-8")
    (log_dir / "home_system.log.1").write_text("old\n", encoding="utf-8")
    (log_dir / "health_check.log").write_text("h\n", encoding="utf-8")
    groups = {g.key: [f.name for f in g.files] for g in lds.list_log_groups()}
    assert groups == {
        "home_system.log": ["home_system.log.1", "home_system.log"],
        "health_check.log": ["health_check.log"],
    }


def test_rotated_file_without_base_is_grouped_under_base_name(log_dir):
    (log_dir / "only.log.1").write_text("x", encoding="utf-8")
    assert [g.key for g in lds.list_log_groups()] == ["only.log"]


def test_resolve_groups_dedupes_and_rejects_unknown(log_dir):
    (log_dir / "a.log").write_text("a", encoding="utf-8")
    assert [g.key for g in lds.resolve_log_groups(["a.log", "a.log"])] == ["a.log"]
    assert lds.resolve_log_groups(["a.log", "../etc/passwd"]) is None
    assert lds.resolve_log_groups(["a.log.1"]) is None  # グループ名は本体名のみ


def test_single_file_download_has_no_header_and_is_masked(api_client, log_dir):
    (log_dir / "home_system.log").write_text("hello token=SECRETVAL\n", encoding="utf-8")
    r = api_client.get(_url("home_system.log"))
    assert r.status_code == 200
    assert r.text == "hello token=<redacted>\n"
    cd = r.headers["content-disposition"]
    assert cd.startswith('attachment; filename="home_system.log.') and cd.endswith('.txt"')
    assert r.headers["content-type"].startswith("text/plain")
    assert r.headers["cache-control"] == "no-store"


def test_multi_download_concatenates_oldest_first_with_headers(api_client, log_dir):
    (log_dir / "home_system.log").write_text("NEW token=S1\n", encoding="utf-8")
    (log_dir / "home_system.log.1").write_text("OLD\n", encoding="utf-8")
    (log_dir / "health_check.log").write_text("HC no newline", encoding="utf-8")
    r = api_client.get(_url("home_system.log", "health_check.log"))
    assert r.status_code == 200
    text = r.text
    assert "S1" not in text
    i_old = text.index("===== home_system.log.1")
    i_new = text.index("===== home_system.log ")
    i_hc = text.index("===== health_check.log")
    assert i_old < i_new and "OLD" in text and "NEW token=<redacted>" in text
    assert "HC no newline\n" in text and i_hc > 0
    assert 'filename="logs_2items.' in r.headers["content-disposition"]


def test_no_selection_is_400(api_client, log_dir):
    assert api_client.get(f"{config.DASHBOARD_BASE_PATH}/sys/logs").status_code == 400


@pytest.mark.parametrize("name", ["missing.log", "..%2f..%2fetc%2fpasswd", "secret.env", "home_system.log.1"])
def test_unknown_group_is_404_even_if_others_are_valid(api_client, log_dir, name):
    (log_dir / "secret.env").write_text("X=1", encoding="utf-8")
    (log_dir / "home_system.log").write_text("x", encoding="utf-8")
    assert api_client.get(_url("home_system.log", name)).status_code == 404


def test_total_size_over_limit_is_413(api_client, log_dir, monkeypatch):
    monkeypatch.setattr(config, "LOG_DOWNLOAD_MAX_MB", 1)
    monkeypatch.setattr(config, "LOG_DOWNLOAD_MAX_TOTAL_MB", 1)
    (log_dir / "a.log").write_bytes(b"x" * 700_000)
    (log_dir / "b.log").write_bytes(b"x" * 700_000)
    assert api_client.get(_url("a.log")).status_code == 200
    assert api_client.get(_url("a.log", "b.log")).status_code == 413


def test_download_404_when_file_disappears_after_listing(api_client, log_dir, monkeypatch):
    path = log_dir / "home_system.log"
    path.write_text("x", encoding="utf-8")
    real_read = lds.read_log_for_download

    def read_after_delete(log_file):
        path.unlink()
        return real_read(log_file)

    monkeypatch.setattr(lds, "read_log_for_download", read_after_delete)
    assert api_client.get(_url("home_system.log")).status_code == 404


def test_sys_page_lists_one_checkbox_per_group(api_client, log_dir):
    (log_dir / "home_system.log").write_text("x", encoding="utf-8")
    (log_dir / "home_system.log.1").write_text("y", encoding="utf-8")
    (log_dir / "health_check.log").write_text("z", encoding="utf-8")
    r = api_client.get(f"{config.DASHBOARD_BASE_PATH}/sys")
    assert r.status_code == 200
    assert 'id="log-download"' in r.text
    assert f'action="{config.DASHBOARD_BASE_PATH}/sys/logs"' in r.text
    assert r.text.count('name="group"') == 2
    assert 'value="home_system.log"' in r.text and 'value="home_system.log.1"' not in r.text
    assert "1つ前" in r.text


def test_sys_page_with_no_logs(api_client, log_dir):
    r = api_client.get(f"{config.DASHBOARD_BASE_PATH}/sys")
    assert r.status_code == 200
    assert "ダウンロードできるログはありません" in r.text
