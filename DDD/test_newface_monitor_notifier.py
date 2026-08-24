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


def test_mass_detection_warning_logged_when_known_casts_exist(caplog):
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

    with caplog.at_level(logging.WARNING, logger="newface_monitor"):
        module.DataManager.load_known_casts = MagicMock(return_value=known_casts)
        module.DataManager.save_known_casts = MagicMock()
        module.DataManager.record_daily_new_casts = MagicMock()
        module._check_site(monitor, notifier, site)

    assert any("Unusually large diff" in record.message for record in caplog.records)
