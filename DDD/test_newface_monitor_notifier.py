# DDD/test_newface_monitor_notifier.py
"""
DiscordNotifier.notify() のembed組み立てに関する回帰テスト。

運用ログにおいて、大量新規検知時にDiscord Webhookが
`400 Client Error: Bad Request ... body: {"embeds": ["0"]}` を返し、
一部キャストの通知が失われる事象が発生した。原因として、遅延読み込み
(lazyload)画像を持つサイトで、実際の画像URLの代わりにプレースホルダー
(例: `data:image/gif;base64,...` のようなdata URI)がCastMember.image_url
として拾われ、Discordのembed thumbnail.urlにhttp(s)以外の値が渡って
バリデーションエラーになるケースを想定している。

本テストは、image_urlがhttp(s)で始まらない場合にthumbnailを省略し、
正常なhttp(s)画像の場合はそのままthumbnailに含めることを検証する。
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

DDD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DDD_DIR))

import newface_monitor as module  # noqa: E402

CastMember = module.CastMember
DiscordNotifier = module.DiscordNotifier


def _make_cast(image_url: str) -> "CastMember":
    return CastMember(
        id="cast-1",
        name="テストキャスト",
        detail_url="https://example.test/profile/1",
        image_url=image_url,
        age="20",
    )


def _notify_and_capture_payload(cast: "CastMember") -> dict:
    notifier = DiscordNotifier(webhook_url="https://discordapp.com/api/webhooks/test")

    response = MagicMock()
    response.raise_for_status.return_value = None
    notifier.session.post = MagicMock(return_value=response)

    notifier.notify([cast], site_name="テストサイト")

    assert notifier.session.post.call_count == 1
    _, kwargs = notifier.session.post.call_args
    return kwargs["json"]


def test_data_uri_image_url_is_dropped_from_thumbnail():
    payload = _notify_and_capture_payload(_make_cast("data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP8="))
    assert payload["embeds"][0]["thumbnail"] == {}


def test_empty_image_url_is_dropped_from_thumbnail():
    payload = _notify_and_capture_payload(_make_cast(""))
    assert payload["embeds"][0]["thumbnail"] == {}


def test_valid_http_image_url_is_kept_in_thumbnail():
    image_url = "https://example.test/images/cast1.jpg"
    payload = _notify_and_capture_payload(_make_cast(image_url))
    assert payload["embeds"][0]["thumbnail"] == {"url": image_url}


def test_mass_detection_warning_logged_when_known_casts_exist(caplog, monkeypatch, tmp_path):
    known_casts = {
        CastMember(id=f"known-{i}", name=f"既存{i}", detail_url="https://example.test/", image_url="")
        for i in range(5)
    }
    new_casts = {
        CastMember(id=f"new-{i}", name=f"新規{i}", detail_url="https://example.test/", image_url="")
        for i in range(module.MonitorConfig.MASS_DETECTION_WARNING_THRESHOLD)
    }
    current_casts = known_casts | new_casts

    site = module.SiteConfig(
        site_id="mass_test",
        name="Mass Test Site",
        target_url="https://example.test/",
        selector_container="div",
        selector_name="li",
        selector_link="a",
        selector_image="img",
    )

    monitor = MagicMock()
    monitor.fetch_current_casts.return_value = current_casts
    notifier = MagicMock()

    import logging

    # ★バグ修正(Issue #122): モノレポ環境ではMY_HOME_SYSTEMがインポート可能なため、
    # newface_monitor.logger は core.logger.setup_logging() が返すロガーになる。
    # このロガーは propagate=False で、独自のStreamHandler/FileHandler等で直接
    # stderr等に出力する設計のため、caplogが依拠するrootロガーへの伝播が起きず、
    # 実際にはログ出力されているのに caplog.records には一切記録されない
    # (DDD単体デプロイ時のフォールバックロガーは logging.getLogger で
    # propagate=Trueが既定のため、このモノレポ環境でのみ恒常的に再現していた)。
    # caplogはロガーのpropagateに依存するため、テスト実行中だけ強制的にTrueへ
    # 切り替え、終了後に元の値へ戻す。
    original_propagate = module.logger.propagate
    module.logger.propagate = True
    try:
        with caplog.at_level(logging.WARNING, logger="newface_monitor"):
            module.DataManager.load_known_casts = MagicMock(return_value=known_casts)
            module.DataManager.save_known_casts = MagicMock()
            module.DataManager.record_daily_new_casts = MagicMock()
            # 素の代入だと他テストファイルへモックがリークするためmonkeypatchを使う
            monkeypatch.setattr(module.DataManager, "clear_site_failure", MagicMock())
            # #364: DataManagerはインスタンス化方式になった(メソッドは上で
            # クラス属性ごとモック済みのため、data_dirの値自体は使われない)
            module._check_site(monitor, notifier, site, module.DataManager(tmp_path))
    finally:
        module.logger.propagate = original_propagate

    assert any("Unusually large diff" in record.message for record in caplog.records)


class TestCheckSiteKnownCastsSaveIsAlwaysUnion:
    """Issue #237の回帰テスト: 新規検知が1件も無い場合にsave_known_castsが
    current_castsで全置換していたため、_parse_htmlが単発でパース失敗した
    既知キャスト(current_castsから漏れているだけで実際には引き続き掲載中)が
    known_castsから恒久的に消え、次回正常にパースできた際に「新規キャスト」
    として誤って再通知される不具合。新規検知の有無に関わらず、save_known_casts
    には常にknown_casts∪current_castsが渡されるべきことを検証する。"""

    def _make_site(self):
        return module.SiteConfig(
            site_id="union_test",
            name="Union Test Site",
            target_url="https://example.test/",
            selector_container="div",
            selector_name="li",
            selector_link="a",
            selector_image="img",
        )

    def test_transient_parse_failure_of_known_cast_does_not_evict_it_when_no_new_casts(
        self, monkeypatch, tmp_path
    ):
        """既知キャストAが単発パース失敗でcurrent_castsに含まれず、かつ
        真の新規キャストも0件だった場合、Aはknown_castsから消えてはならない。
        current_castsを完全に空にすると「スクレイピング自体に失敗した」扱いの
        早期return分岐(if not current_casts: return)に入ってしまうため、
        既知キャストBは正常にパースできたという設定にして区別する。"""
        cast_a = CastMember(id="a", name="既存A", detail_url="https://example.test/a", image_url="")
        cast_b = CastMember(id="b", name="既存B", detail_url="https://example.test/b", image_url="")
        known_casts = {cast_a, cast_b}
        current_casts = {cast_b}  # 今回はAのパースに失敗、Bは成功、新規は0件

        site = self._make_site()
        monitor = MagicMock()
        monitor.fetch_current_casts.return_value = current_casts
        notifier = MagicMock()

        monkeypatch.setattr(module.DataManager, "load_known_casts", MagicMock(return_value=known_casts))
        mock_save = MagicMock()
        monkeypatch.setattr(module.DataManager, "save_known_casts", mock_save)
        monkeypatch.setattr(module.DataManager, "record_daily_new_casts", MagicMock())
        monkeypatch.setattr(module.DataManager, "clear_site_failure", MagicMock())

        module._check_site(monitor, notifier, site, module.DataManager(tmp_path))

        mock_save.assert_called_once()
        saved_site, saved_casts = mock_save.call_args[0]
        assert cast_a in saved_casts, "単発パース失敗した既知キャストが全置換でknown_castsから消えている"

    def test_still_unions_when_genuine_new_casts_are_detected(self, monkeypatch, tmp_path):
        """新規検知がある場合も、既存の挙動通りunionで保存されること(非破壊確認)。"""
        cast_a = CastMember(id="a", name="既存A", detail_url="https://example.test/a", image_url="")
        cast_new = CastMember(id="new-1", name="新規1", detail_url="https://example.test/new-1", image_url="")
        known_casts = {cast_a}
        current_casts = {cast_new}  # Aは今回パース失敗、newは真の新規

        site = self._make_site()
        monitor = MagicMock()
        monitor.fetch_current_casts.return_value = current_casts
        notifier = MagicMock()

        monkeypatch.setattr(module.DataManager, "load_known_casts", MagicMock(return_value=known_casts))
        mock_save = MagicMock()
        monkeypatch.setattr(module.DataManager, "save_known_casts", mock_save)
        monkeypatch.setattr(module.DataManager, "record_daily_new_casts", MagicMock())
        monkeypatch.setattr(module.DataManager, "clear_site_failure", MagicMock())

        module._check_site(monitor, notifier, site, module.DataManager(tmp_path))

        notifier.notify.assert_called_once()
        mock_save.assert_called_once()
        saved_site, saved_casts = mock_save.call_args[0]
        assert saved_casts == {cast_a, cast_new}


class TestNotifyDailySummaryReturnValue:
    """Issue #226の回帰テスト: notify_daily_summaryは送信結果をbool値として
    呼び出し元に返す(以前は常にNoneで、呼び出し元が成否を判別できなかった)。"""

    def test_returns_true_on_success(self):
        notifier = DiscordNotifier(webhook_url="https://discordapp.com/api/webhooks/test")
        response = MagicMock()
        response.raise_for_status.return_value = None
        notifier.session.post = MagicMock(return_value=response)

        result = notifier.notify_daily_summary({"site_a": 2}, {"site_a": "サイトA"}, "2026-08-30")

        assert result is True

    def test_returns_false_on_request_exception(self):
        import requests

        notifier = DiscordNotifier(webhook_url="https://discordapp.com/api/webhooks/test")
        notifier.session.post = MagicMock(side_effect=requests.RequestException("boom"))

        result = notifier.notify_daily_summary({"site_a": 2}, {"site_a": "サイトA"}, "2026-08-30")

        assert result is False

    def test_returns_false_when_webhook_not_configured(self):
        notifier = DiscordNotifier(webhook_url="")

        result = notifier.notify_daily_summary({"site_a": 2}, {"site_a": "サイトA"}, "2026-08-30")

        assert result is False


def _make_casts(n: int):
    return [
        _make_cast("") for _ in range(n)
    ]


class TestDiscordNotifierCircuitBreaker:
    """DiscordNotifierがWebhookへの連続送信失敗時にサーキットブレーカーとして
    機能することの回帰テスト。以前は401/404の一部ケースを除き、タイムアウトや
    接続エラーが続いてもnotify()は無制限にリトライし続けていた。"""

    def test_notify_skips_remaining_casts_after_consecutive_request_exceptions(self, monkeypatch):
        import requests

        monkeypatch.setattr(module.time, "sleep", lambda *_: None)
        notifier = DiscordNotifier(webhook_url="https://discordapp.com/api/webhooks/test")
        notifier.session.post = MagicMock(side_effect=requests.exceptions.ConnectionError("boom"))

        # 既定の閾値(3)を超える件数を渡し、閾値到達後は送信自体が行われないことを確認する
        casts = _make_casts(6)
        notifier.notify(casts, site_name="テストサイト")

        # デフォルトの閾値(3)に達した時点でブレーカーが開き、以降の送信はスキップされる
        assert notifier.session.post.call_count == 3
        assert notifier._circuit_breaker.is_open is True

    def test_notify_trips_breaker_immediately_on_401(self, monkeypatch):
        import requests

        monkeypatch.setattr(module.time, "sleep", lambda *_: None)
        notifier = DiscordNotifier(webhook_url="https://discordapp.com/api/webhooks/test")
        response = MagicMock()
        response.status_code = 401
        response.text = "invalid webhook"
        http_error = requests.exceptions.HTTPError(response=response)
        notifier.session.post = MagicMock(side_effect=http_error)

        notifier.notify(_make_casts(3), site_name="テストサイト")

        # 401は再試行が無意味なため、1回目の失敗で即座にブレーカーが開き打ち切られる
        assert notifier.session.post.call_count == 1
        assert notifier._circuit_breaker.is_open is True

    def test_notify_daily_summary_is_skipped_when_breaker_is_open(self):
        notifier = DiscordNotifier(webhook_url="https://discordapp.com/api/webhooks/test")
        notifier.session.post = MagicMock()
        notifier._circuit_breaker.trip()

        result = notifier.notify_daily_summary({"site_a": 1}, {"site_a": "サイトA"}, "2026-08-30")

        assert result is False
        notifier.session.post.assert_not_called()

    def test_notify_daily_summary_success_resets_breaker(self):
        notifier = DiscordNotifier(webhook_url="https://discordapp.com/api/webhooks/test")
        response = MagicMock()
        response.raise_for_status.return_value = None
        notifier.session.post = MagicMock(return_value=response)
        notifier._circuit_breaker.record_failure()
        notifier._circuit_breaker.record_failure()

        result = notifier.notify_daily_summary({"site_a": 1}, {"site_a": "サイトA"}, "2026-08-30")

        assert result is True
        assert notifier._circuit_breaker.is_open is False
