# MY_HOME_SYSTEM/tests/test_mfujin_blog_monitor.py
"""
monitors/mfujin_blog_monitor.py のオーケストレーション(重複送信防止・失敗時のDB記録・
除外カテゴリのスキップ・異常検知時のDiscordエラー通知)を検証する。

実際のHTTP通信・LINE送信は行わず、services.mfujin_blog_service側の関数と
services.notification_service.send_push をmonkeypatchして検証する。DBは
tests/conftest.py の isolated_db フィクスチャ(テストごとの一時SQLiteファイル)を使う。
"""
import config
import monitors.mfujin_blog_monitor as target
import services.mfujin_blog_service as blog
from core.database import get_db_cursor


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
        blog, "fetch_latest_article_summary", lambda session: blog.ArticleSummary(url=article.url, title=article.title)
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
        blog, "fetch_latest_article_summary", lambda session: blog.ArticleSummary(url=article.url, title=article.title)
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
        blog, "fetch_latest_article_summary", lambda session: blog.ArticleSummary(url=article.url, title=article.title)
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
        blog, "fetch_latest_article_summary", lambda session: blog.ArticleSummary(url=article.url, title=article.title)
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
        blog, "fetch_latest_article_summary", lambda session: blog.ArticleSummary(url=article.url, title=article.title)
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
        blog, "fetch_latest_article_summary", lambda session: blog.ArticleSummary(url=article.url, title=article.title)
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

    def _raise(session):
        raise ConnectionError("boom")

    monkeypatch.setattr(blog, "fetch_latest_article_summary", _raise)

    error_calls = []
    monkeypatch.setattr(target, "_notify_operational_error", lambda msg: error_calls.append(msg))

    target.run()

    assert len(error_calls) == 1
    with get_db_cursor() as cur:
        cur.execute(f"SELECT COUNT(*) as c FROM {config.SQLITE_TABLE_MFUJIN_NOTIFICATIONS}")
        assert cur.fetchone()["c"] == 0
