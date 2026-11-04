# MY_HOME_SYSTEM/tests/test_mfujin_blog_monitor.py
"""
monitors/mfujin_blog_monitor.py のオーケストレーション(重複送信防止・失敗時のDB記録・
除外カテゴリのスキップ・異常検知時のDiscordエラー通知)を検証する。

実際のHTTP通信・LINE送信は行わず、services.mfujin_blog_service側の関数と
services.notification_service.send_push をmonkeypatchして検証する。DBは
tests/conftest.py の isolated_db フィクスチャ(テストごとの一時SQLiteファイル)を使う。
"""
import dataclasses

import config
import pytest
import monitors.mfujin_blog_monitor as target
import services.mfujin_blog_service as blog
from core.database import get_db_cursor


@pytest.fixture(autouse=True)
def _single_site(monkeypatch):
    """既存テストは1サイト前提。複数サイト巡回は専用テストで個別に設定する。"""
    monkeypatch.setattr(config, "MFUJIN_BLOG_SITES", [("えむふじん", "https://mfujin.test/")])


def _make_article(url="https://mfujin.test/archives/dummy.html", category="日常のひとこま", image_urls=None):
    return blog.ArticleContent(
        url=url,
        title="ダミー記事タイトル",
        published_at="2026-09-28T07:01:00+09:00",
        category=category,
        image_urls=image_urls if image_urls is not None else ["https://cdn.mfujin.test/imgs/page1.jpg"],
    )


def _fetch_row(url):
    with get_db_cursor() as cur:
        cur.execute(
            f"SELECT * FROM {config.SQLITE_TABLE_MFUJIN_NOTIFICATIONS} WHERE article_url = ?",
            (url,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def test_run_sends_and_records_success(isolated_db, monkeypatch):
    article = _make_article()
    monkeypatch.setattr(blog, "build_session", lambda: object())
    monkeypatch.setattr(
        blog, "fetch_latest_article_summary", lambda session, *a: blog.ArticleSummary(url=article.url, title=article.title)
    )
    monkeypatch.setattr(blog, "fetch_article_content", lambda session, url: article)
    monkeypatch.setattr(blog, "validate_image_urls", lambda session, urls: urls)

    sent_calls = []
    monkeypatch.setattr(
        target,
        "send_push",
        lambda **kwargs: sent_calls.append(kwargs) or True,
    )

    target.run()

    assert len(sent_calls) == 1
    assert sent_calls[0]["target"] == "line"
    row = _fetch_row(article.url)
    assert row is not None
    assert row["status"] == "sent"
    assert row["sent_at"] is not None
    assert row["image_count"] == 1


def test_run_does_not_resend_already_sent_article(isolated_db, monkeypatch):
    article = _make_article()
    monkeypatch.setattr(blog, "build_session", lambda: object())
    monkeypatch.setattr(
        blog, "fetch_latest_article_summary", lambda session, *a: blog.ArticleSummary(url=article.url, title=article.title)
    )

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("既送信記事なのに記事本文取得が呼ばれてはいけない")

    monkeypatch.setattr(blog, "fetch_article_content", _fail_if_called)

    send_calls = []
    monkeypatch.setattr(target, "send_push", lambda **kwargs: send_calls.append(kwargs) or True)

    # 1回目: 送信成功として記録させる
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            f"""
            INSERT INTO {config.SQLITE_TABLE_MFUJIN_NOTIFICATIONS}
                (article_url, article_title, published_at, fetched_at, sent_at, status, image_count)
            VALUES (?, ?, ?, ?, ?, 'sent', 1)
            """,
            (article.url, article.title, article.published_at, "2026-09-28T07:00:00+09:00", "2026-09-28T07:00:00+09:00"),
        )

    target.run()

    assert send_calls == []  # 二重送信していない


def test_run_retries_after_previous_failure(isolated_db, monkeypatch):
    article = _make_article()
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            f"""
            INSERT INTO {config.SQLITE_TABLE_MFUJIN_NOTIFICATIONS}
                (article_url, article_title, published_at, fetched_at, sent_at, status, image_count, error_detail)
            VALUES (?, ?, ?, ?, NULL, 'failed', 1, 'LINE送信に失敗しました')
            """,
            (article.url, article.title, article.published_at, "2026-09-28T07:00:00+09:00"),
        )

    monkeypatch.setattr(blog, "build_session", lambda: object())
    monkeypatch.setattr(
        blog, "fetch_latest_article_summary", lambda session, *a: blog.ArticleSummary(url=article.url, title=article.title)
    )
    monkeypatch.setattr(blog, "fetch_article_content", lambda session, url: article)
    monkeypatch.setattr(blog, "validate_image_urls", lambda session, urls: urls)
    monkeypatch.setattr(target, "send_push", lambda **kwargs: True)

    target.run()

    row = _fetch_row(article.url)
    assert row["status"] == "sent"  # 前回失敗した記事が再送で成功扱いに更新される


def test_run_skips_excluded_category_without_error_notification(isolated_db, monkeypatch):
    article = _make_article(category="PR記事")
    monkeypatch.setattr(blog, "build_session", lambda: object())
    monkeypatch.setattr(
        blog, "fetch_latest_article_summary", lambda session, *a: blog.ArticleSummary(url=article.url, title=article.title)
    )
    monkeypatch.setattr(blog, "fetch_article_content", lambda session, url: article)

    error_calls = []
    monkeypatch.setattr(target, "_notify_operational_error", lambda msg: error_calls.append(msg))
    send_calls = []
    monkeypatch.setattr(target, "send_push", lambda **kwargs: send_calls.append(kwargs) or True)

    target.run()

    assert send_calls == []  # LINEには送らない
    assert error_calls == []  # 正常なスキップなのでエラー通知もしない
    row = _fetch_row(article.url)
    assert row["status"] == "skipped_category"


def test_run_notifies_error_when_non_excluded_category_has_zero_images(isolated_db, monkeypatch):
    article = _make_article(category="日常のひとこま", image_urls=[])
    monkeypatch.setattr(blog, "build_session", lambda: object())
    monkeypatch.setattr(
        blog, "fetch_latest_article_summary", lambda session, *a: blog.ArticleSummary(url=article.url, title=article.title)
    )
    monkeypatch.setattr(blog, "fetch_article_content", lambda session, url: article)

    error_calls = []
    monkeypatch.setattr(target, "_notify_operational_error", lambda msg: error_calls.append(msg))
    send_calls = []
    monkeypatch.setattr(target, "send_push", lambda **kwargs: send_calls.append(kwargs) or True)

    target.run()

    assert send_calls == []
    assert len(error_calls) == 1  # HTML構造変化の疑いとしてエラー通知する
    row = _fetch_row(article.url)
    assert row["status"] == "skipped_no_images"


def test_run_records_failed_status_when_line_send_fails(isolated_db, monkeypatch):
    article = _make_article()
    monkeypatch.setattr(blog, "build_session", lambda: object())
    monkeypatch.setattr(
        blog, "fetch_latest_article_summary", lambda session, *a: blog.ArticleSummary(url=article.url, title=article.title)
    )
    monkeypatch.setattr(blog, "fetch_article_content", lambda session, url: article)
    monkeypatch.setattr(blog, "validate_image_urls", lambda session, urls: urls)
    monkeypatch.setattr(target, "send_push", lambda **kwargs: False)

    target.run()

    row = _fetch_row(article.url)
    assert row["status"] == "failed"
    assert row["sent_at"] is None  # 失敗をDBに成功扱いで記録しない


def test_run_notifies_error_on_network_failure_and_records_nothing(isolated_db, monkeypatch):
    monkeypatch.setattr(blog, "build_session", lambda: object())

    def _raise(session, *a):
        raise blog.requests.ConnectionError("boom")

    monkeypatch.setattr(blog, "fetch_latest_article_summary", _raise)

    error_calls = []
    monkeypatch.setattr(target, "_notify_operational_error", lambda msg: error_calls.append(msg))

    target.run()

    assert len(error_calls) == 1
    with get_db_cursor() as cur:
        cur.execute(f"SELECT COUNT(*) as c FROM {config.SQLITE_TABLE_MFUJIN_NOTIFICATIONS}")
        assert cur.fetchone()["c"] == 0


def test_run_processes_every_site_and_isolates_failures(isolated_db, monkeypatch):
    """全サイトを巡回し、1サイトの取得失敗が他サイトの通知を止めない。ラベルが通知に渡る。"""
    monkeypatch.setattr(
        config,
        "MFUJIN_BLOG_SITES",
        [("A", "https://a.test/"), ("B", "https://b.test/"), ("C", "https://c.test/")],
    )
    monkeypatch.setattr(blog, "build_session", lambda: object())

    def _summary(session, top_url):
        if top_url == "https://b.test/":
            raise blog.requests.ConnectionError("boom")
        return blog.ArticleSummary(url=top_url + "p.html", title="t")

    monkeypatch.setattr(blog, "fetch_latest_article_summary", _summary)
    monkeypatch.setattr(blog, "fetch_article_content", lambda session, url: _make_article(url=url))
    monkeypatch.setattr(blog, "validate_image_urls", lambda session, urls: urls)

    labels = []
    real_build = blog.build_line_messages
    monkeypatch.setattr(blog, "build_line_messages", lambda art, label: labels.append(label) or real_build(art, label))
    monkeypatch.setattr(target, "send_push", lambda **kw: True)
    error_calls = []
    monkeypatch.setattr(target, "_notify_operational_error", lambda msg: error_calls.append(msg))

    target.run()

    assert labels == ["A", "C"]
    assert _fetch_row("https://a.test/p.html")["status"] == "sent"
    assert _fetch_row("https://c.test/p.html")["status"] == "sent"
    assert len(error_calls) == 1 and error_calls[0].startswith("[B]")


def test_default_sites_include_added_blogs():
    urls = [u for _, u in config.MFUJIN_BLOG_EXTRA_SITES]
    assert "https://howawand.blog.jp/" in urls
    assert "https://yashiroazuki.blog.jp/" in urls


# --- Issue #1087: 再送回数の上限・タイトルが取れない記事 ---


def _patch_site(monkeypatch, article, *, summary_title=None):
    monkeypatch.setattr(blog, "build_session", lambda: object())
    monkeypatch.setattr(
        blog,
        "fetch_latest_article_summary",
        lambda session, *a: blog.ArticleSummary(
            url=article.url, title=article.title if summary_title is None else summary_title
        ),
    )
    monkeypatch.setattr(blog, "fetch_article_content", lambda session, url: article)
    monkeypatch.setattr(blog, "validate_image_urls", lambda session, urls: urls)


def test_run_counts_attempts_and_stops_resending_at_limit(isolated_db, monkeypatch):
    # 400 のように再試行しても直らない失敗: 上限回数で failed_permanent にし、以後は再送しない
    monkeypatch.setattr(config, "MFUJIN_BLOG_MAX_SEND_ATTEMPTS", 3)
    article = _make_article()
    _patch_site(monkeypatch, article)
    send_calls = []
    monkeypatch.setattr(target, "send_push", lambda **kwargs: send_calls.append(kwargs) and False)
    error_calls = []
    monkeypatch.setattr(target, "_notify_operational_error", lambda msg: error_calls.append(msg))

    target.run()
    target.run()
    row = _fetch_row(article.url)
    assert (row["status"], row["attempt_count"]) == ("failed", 2)
    assert error_calls == []  # 上限に達するまでは停止の通知を出さない

    target.run()
    row = _fetch_row(article.url)
    assert (row["status"], row["attempt_count"]) == ("failed_permanent", 3)
    assert len(error_calls) == 1
    assert "再送を停止" in error_calls[0]

    target.run()  # 以後の巡回では送信も通知もしない
    assert len(send_calls) == 3
    assert len(error_calls) == 1


def test_run_records_attempt_count_on_success_after_failure(isolated_db, monkeypatch):
    article = _make_article()
    _patch_site(monkeypatch, article)
    results = iter([False, True])
    monkeypatch.setattr(target, "send_push", lambda **kwargs: next(results))

    target.run()
    target.run()

    row = _fetch_row(article.url)
    assert (row["status"], row["attempt_count"]) == ("sent", 2)


def test_run_uses_summary_title_and_warns_when_article_title_missing(isolated_db, monkeypatch):
    article = dataclasses.replace(_make_article(), title="")
    _patch_site(monkeypatch, article, summary_title="一覧ページのタイトル")
    sent_calls = []
    monkeypatch.setattr(target, "send_push", lambda **kwargs: sent_calls.append(kwargs) or True)
    warning_calls = []
    monkeypatch.setattr(target, "_notify_operational_warning", lambda msg: warning_calls.append(msg))

    target.run()

    assert len(warning_calls) == 1
    assert "タイトルを取得できませんでした" in warning_calls[0]
    flex = sent_calls[0]["messages"][0]
    assert flex.contents.contents[0].body.contents[1].text == "一覧ページのタイトル"
    row = _fetch_row(article.url)
    assert (row["status"], row["article_title"]) == ("sent", "一覧ページのタイトル")


def test_run_sends_placeholder_when_no_title_anywhere(isolated_db, monkeypatch):
    article = dataclasses.replace(_make_article(), title="")
    _patch_site(monkeypatch, article, summary_title="")
    sent_calls = []
    monkeypatch.setattr(target, "send_push", lambda **kwargs: sent_calls.append(kwargs) or True)
    monkeypatch.setattr(target, "_notify_operational_warning", lambda msg: None)

    target.run()

    flex = sent_calls[0]["messages"][0]
    assert flex.contents.contents[0].body.contents[1].text == blog.TITLE_PLACEHOLDER


def test_notify_operational_warning_logs_warning_and_pushes_discord(monkeypatch, caplog):
    calls = []
    monkeypatch.setattr(target, "send_push", lambda **kwargs: calls.append(kwargs) or True)

    with caplog.at_level("WARNING"):
        target._notify_operational_warning("テスト警告")

    assert calls[0]["target"] == "discord"
    assert calls[0]["channel"] == "error"
    assert any(r.levelname == "WARNING" and "テスト警告" in r.getMessage() for r in caplog.records)
