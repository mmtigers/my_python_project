# MY_HOME_SYSTEM/tests/test_release_history_service.py
"""services/release_history_service.py(全アプリの更新履歴の集約)のテスト。

外部(あさノート・よるノート)へは実際に接続しない。`requests.get` / `_fetch_remote` を差し替える。
"""
import json
import os
import sys

import pytest
import requests

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services import release_history_service as svc

ASA_RAW = [
    {"version": "0.4.21", "date": "2026-10-09", "title": "反映されるように", "summary": "要約", "details": ["a", "b"]},
    {"version": "0.4.20", "date": "2026-10-08", "title": "読み上げ対応", "summary": "", "details": []},
]
YORU_RAW = [{"version": "0.3.9", "date": "2026-10-09", "title": "よる", "summary": "s", "details": ["x"]}]


@pytest.fixture(autouse=True)
def _fresh_cache():
    svc.clear_cache()
    yield
    svc.clear_cache()


def _remote(monkeypatch, asa=ASA_RAW, yoru=YORU_RAW, calls=None):
    def fake(base_url, app):
        if calls is not None:
            calls.append(app)
        raw = asa if app == svc.KEY_ASA else yoru
        if isinstance(raw, Exception):
            raise raw
        return svc.normalize_entries(raw, app)
    monkeypatch.setattr(svc, "_fetch_remote", fake)


class TestNormalizeEntries:
    def test_common_shape(self):
        out = svc.normalize_entries(ASA_RAW, svc.KEY_ASA)
        assert out[0] == {
            "app": "asa", "app_label": "あさノート", "version": "0.4.21", "date": "2026-10-09",
            "title": "反映されるように", "summary": "要約", "details": ["a", "b"],
        }

    def test_quest_changes_become_details(self):
        out = svc.normalize_entries([{"version": "1.2.0", "date": "2026-10-03", "changes": ["x", "y"]}], svc.KEY_QUEST)
        assert out[0]["details"] == ["x", "y"] and out[0]["title"] == ""

    @pytest.mark.parametrize("raw", [None, {}, "text", 5, [None], [1, "a"], [{"date": "2026/10/09", "title": "t"}],
                                     [{"date": "2026-10-09"}], [{"title": "no date"}]])
    def test_invalid_input_yields_no_entries(self, raw):
        assert svc.normalize_entries(raw, svc.KEY_ASA) == []

    def test_text_is_truncated_and_counts_capped(self):
        long = "あ" * 10_000
        raw = [{"date": "2026-10-09", "title": long, "details": [long] * 100}] * 100
        out = svc.normalize_entries(raw, svc.KEY_ASA)
        assert len(out) == svc.MAX_ENTRIES_PER_SOURCE
        assert len(out[0]["title"]) == svc.MAX_TEXT_LEN
        assert len(out[0]["details"]) == svc.MAX_DETAILS_PER_ENTRY
        assert all(len(d) == svc.MAX_TEXT_LEN for d in out[0]["details"])

    def test_non_string_values_do_not_crash(self):
        out = svc.normalize_entries([{"date": "2026-10-09", "title": 5, "summary": ["x"], "details": [1, None, "ok"]}], svc.KEY_ASA)
        assert out and out[0]["details"] == ["ok"] and out[0]["title"] == ""


class TestRealDataFiles:
    """手で編集するJSONが壊れて、エントリが黙って落ちないこと。"""

    def test_dashboard_releases_json_is_fully_valid_and_newest_first(self):
        with open(svc.dashboard_releases_path(), encoding="utf-8") as f:
            raw = json.load(f)
        out = svc.normalize_entries(raw, svc.KEY_DASHBOARD)
        assert len(out) == len(raw) > 0
        dates = [e["date"] for e in out]
        assert dates == sorted(dates, reverse=True)

    def test_quest_changelog_json_is_fully_valid_and_newest_first(self):
        with open(svc.quest_changelog_path(), encoding="utf-8") as f:
            raw = json.load(f)
        out = svc.normalize_entries(raw, svc.KEY_QUEST)
        assert len(out) == len(raw) > 0
        dates = [e["date"] for e in out]
        assert dates == sorted(dates, reverse=True)
        assert len({e["version"] for e in out}) == len(out)


class TestGetReleaseHistory:
    def test_merges_all_four_apps_newest_first(self, monkeypatch):
        _remote(monkeypatch)
        history = svc.get_release_history()
        assert [s["app"] for s in history["sources"]] == ["dashboard", "quest", "asa", "yoru"]
        assert all(s["ok"] for s in history["sources"])
        dates = [e["date"] for e in history["entries"]]
        assert dates == sorted(dates, reverse=True)
        assert {e["app"] for e in history["entries"]} == {"dashboard", "quest", "asa", "yoru"}

    def test_same_date_orders_by_app_then_keeps_file_order(self, monkeypatch):
        _remote(monkeypatch)
        entries = [e for e in svc.get_release_history()["entries"] if e["date"] == "2026-10-09"]
        assert [e["app"] for e in entries][:3] == ["quest", "asa", "yoru"]

    def test_remote_failure_keeps_local_apps_and_flags_the_failed_ones(self, monkeypatch):
        _remote(monkeypatch, asa=requests.exceptions.ConnectionError("down"), yoru=YORU_RAW)
        history = svc.get_release_history()
        by_app = {s["app"]: s for s in history["sources"]}
        assert by_app["asa"]["ok"] is False and by_app["asa"]["entries"] == []
        assert "取得できませんでした" in by_app["asa"]["error"]
        assert by_app["dashboard"]["ok"] and by_app["quest"]["ok"] and by_app["yoru"]["ok"]
        assert history["entries"]

    def test_success_is_cached(self, monkeypatch):
        calls = []
        _remote(monkeypatch, calls=calls)
        svc.get_release_history()
        svc.get_release_history()
        assert sorted(calls) == ["asa", "yoru"]

    def test_failure_is_remembered_briefly_so_pages_do_not_block_again(self, monkeypatch):
        calls = []
        _remote(monkeypatch, asa=TimeoutError("slow"), calls=calls)
        svc.get_release_history()
        svc.get_release_history()
        assert calls.count("asa") == 1

    def test_stale_content_is_returned_after_a_later_failure(self, monkeypatch):
        _remote(monkeypatch)
        svc.get_release_history()
        # キャッシュの期限切れを模擬し、次の取得を失敗させる
        monkeypatch.setattr(svc, "CACHE_TTL_SEC", 0.0)
        _remote(monkeypatch, asa=RuntimeError("down"))
        asa = {s["app"]: s for s in svc.get_release_history()["sources"]}["asa"]
        assert asa["ok"] is False and asa["stale"] is True
        assert [e["version"] for e in asa["entries"]] == ["0.4.21", "0.4.20"]
        assert "最後に取得できた内容" in asa["error"]

    def test_missing_local_file_fails_only_that_app(self, monkeypatch, tmp_path):
        _remote(monkeypatch)
        monkeypatch.setattr(svc, "dashboard_releases_path", lambda: str(tmp_path / "nope.json"))
        by_app = {s["app"]: s for s in svc.get_release_history()["sources"]}
        assert by_app["dashboard"]["ok"] is False and by_app["quest"]["ok"] is True

    def test_corrupt_local_file_is_a_failure_not_a_crash(self, monkeypatch, tmp_path):
        _remote(monkeypatch)
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        monkeypatch.setattr(svc, "quest_changelog_path", lambda: str(bad))
        by_app = {s["app"]: s for s in svc.get_release_history()["sources"]}
        assert by_app["quest"]["ok"] is False


class _FakeResponse:
    def __init__(self, body: bytes, status=200):
        self._body = body
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}")

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestFetchRemote:
    def test_fetches_releases_json_under_the_app_url_with_a_timeout(self, monkeypatch):
        seen = {}

        def fake_get(url, timeout, stream):
            seen.update(url=url, timeout=timeout, stream=stream)
            return _FakeResponse(json.dumps(ASA_RAW).encode())

        monkeypatch.setattr(svc.requests, "get", fake_get)
        out = svc._fetch_remote("https://asa-note.vercel.app/", svc.KEY_ASA)
        assert seen["url"] == "https://asa-note.vercel.app/releases.json"
        assert seen["timeout"] == svc.REMOTE_TIMEOUT_SEC and seen["stream"] is True
        assert len(out) == 2

    def test_base_url_without_trailing_slash_still_works(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(svc.requests, "get", lambda url, **k: seen.update(url=url) or _FakeResponse(b"[]"))
        svc._fetch_remote("https://yorunote-mm.vercel.app", svc.KEY_YORU)
        assert seen["url"] == "https://yorunote-mm.vercel.app/releases.json"

    def test_http_error_raises(self, monkeypatch):
        monkeypatch.setattr(svc.requests, "get", lambda url, **k: _FakeResponse(b"", status=404))
        with pytest.raises(requests.exceptions.HTTPError):
            svc._fetch_remote("https://x.example/", svc.KEY_ASA)

    def test_oversized_response_is_rejected(self, monkeypatch):
        monkeypatch.setattr(svc.requests, "get",
                            lambda url, **k: _FakeResponse(b"[" + b" " * (svc.MAX_RESPONSE_BYTES + 10) + b"]"))
        with pytest.raises(ValueError):
            svc._fetch_remote("https://x.example/", svc.KEY_ASA)

    def test_non_json_body_raises(self, monkeypatch):
        monkeypatch.setattr(svc.requests, "get", lambda url, **k: _FakeResponse(b"<html>login</html>"))
        with pytest.raises(ValueError):
            svc._fetch_remote("https://x.example/", svc.KEY_ASA)

    def test_html_login_page_through_get_release_history_is_a_failure(self, monkeypatch):
        monkeypatch.setattr(svc.requests, "get", lambda url, **k: _FakeResponse(b"<html>login</html>"))
        by_app = {s["app"]: s for s in svc.get_release_history()["sources"]}
        assert by_app["asa"]["ok"] is False and by_app["yoru"]["ok"] is False
