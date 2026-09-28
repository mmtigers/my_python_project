# MY_HOME_SYSTEM/tests/test_mfujin_blog_service.py
"""
services/mfujin_blog_service.py のパーサ/判定/メッセージ組み立てロジックの単体テスト。

ネットワークアクセスは行わず、実サイトのHTML構造(2026-09に実際の記事HTMLで確認済み:
`.pict`クラスかつ拡張子`.gif`以外が本編漫画ページ、`dd.article-category1`がカテゴリ等)を
模した最小限のfixture HTML文字列だけでBeautifulSoupのパース部分を検証する。
fixture中のタイトル・alt文字列はすべてテスト用のダミー値であり、実際のブログ記事の
文言ではない。
"""
import services.mfujin_blog_service as blog
from bs4 import BeautifulSoup
from linebot.v3.messaging import FlexMessage, ImageMessage, TextMessage

TOP_PAGE_HTML = """
<html><body>
<article class="article first-article">
  <h1 class="article-title"><a href="https://mfujin.test/archives/latest_dummy.html">ダミー記事タイトルA</a></h1>
</article>
<article class="article">
  <h1 class="article-title"><a href="https://mfujin.test/archives/older_dummy.html">ダミー記事タイトルB</a></h1>
</article>
</body></html>
"""

TOP_PAGE_HTML_BROKEN = """
<html><body>
<div class="not-an-article">構造が変わってしまったダミーページ</div>
</body></html>
"""


def _article_html(*, category="日常のひとこま", extra_body_more=""):
    return f"""
    <html><body>
    <article class="article first-article">
      <h1 class="article-title"><a href="https://mfujin.test/archives/dummy.html">ダミー記事タイトル</a></h1>
      <time datetime="2026-09-28T07:01:00+09:00" itemprop="datePublished">2026/09/28</time>
      <dd class="article-category1">{category}</dd>
      <div class="article-body">
        <div class="article-body-inner">
          <p>
            <img src="https://cdn.mfujin.test/imgs/header.gif" class="pict" width="1200" height="240" alt="ヘッダーバナー">
            <img src="https://cdn.mfujin.test/imgs/icon1.jpg" alt="キャラA">
            <img src="https://cdn.mfujin.test/imgs/icon2.jpg" alt="キャラB">
          </p>
          <div class="article-body-more" id="more">
            <img src="https://cdn.mfujin.test/imgs/page1.jpg" class="pict" width="1200" height="3009" alt="ページ1">
            <img src="https://cdn.mfujin.test/imgs/page2.jpg" class="pict" width="1200" height="1500" alt="ページ2">
            <img src="https://cdn.mfujin.test/imgs/promo.jpg" alt="既刊のお知らせ">
            <img src="https://cdn.mfujin.test/imgs/footer.gif" class="pict" width="1200" height="260" alt="フッターバナー">
            {extra_body_more}
          </div>
        </div>
      </div>
    </article>
    </body></html>
    """


ARTICLE_HTML_NO_BODY_CONTAINER = """
<html><body>
<article class="article first-article">
  <h1 class="article-title"><a href="https://mfujin.test/archives/dummy.html">ダミー記事タイトル</a></h1>
</article>
</body></html>
"""


def test_parse_latest_article_summary_returns_first_article():
    soup = BeautifulSoup(TOP_PAGE_HTML, "html.parser")
    summary = blog.parse_latest_article_summary(soup)
    assert summary.url == "https://mfujin.test/archives/latest_dummy.html"
    assert summary.title == "ダミー記事タイトルA"


def test_parse_latest_article_summary_raises_on_structure_change():
    soup = BeautifulSoup(TOP_PAGE_HTML_BROKEN, "html.parser")
    try:
        blog.parse_latest_article_summary(soup)
        assert False, "MfujinBlogError が送出されるべき"
    except blog.MfujinBlogError:
        pass


def test_parse_article_content_extracts_only_manga_pages_in_order():
    soup = BeautifulSoup(_article_html(), "html.parser")
    article = blog.parse_article_content(soup, "https://mfujin.test/archives/dummy.html")

    assert article.title == "ダミー記事タイトル"
    assert article.published_at == "2026-09-28T07:01:00+09:00"
    assert article.category == "日常のひとこま"
    # ヘッダー/フッターのgifバナー・キャラアイコン・既刊お知らせ画像は除外され、
    # 本編のpict(.jpg)画像だけが表示順どおりに残る。
    assert article.image_urls == [
        "https://cdn.mfujin.test/imgs/page1.jpg",
        "https://cdn.mfujin.test/imgs/page2.jpg",
    ]


def test_parse_article_content_raises_when_body_container_missing():
    soup = BeautifulSoup(ARTICLE_HTML_NO_BODY_CONTAINER, "html.parser")
    try:
        blog.parse_article_content(soup, "https://mfujin.test/archives/dummy.html")
        assert False, "MfujinBlogError が送出されるべき"
    except blog.MfujinBlogError:
        pass


class _FakeFetchResponse:
    """requests.Response の代わり。.content(生バイト列)のみを模倣する。"""

    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


class _FakeSessionForFetch:
    """fetch_*系がresp.contentから直接BeautifulSoupへ渡す経路を検証するための
    最小限のセッション代替。requestsの`resp.encoding`(HTTPヘッダのcharset)を
    経由しないため、BeautifulSoup側のエンコーディング自動判定に処理が委ねられる
    実際の呼び出し経路を再現する。"""

    def __init__(self, content: bytes):
        self._content = content

    def get(self, url, timeout=None):
        return _FakeFetchResponse(self._content)


def test_fetch_latest_article_summary_decodes_utf8_bytes_correctly(monkeypatch):
    """2026-09-28の実機検証で、記事タイトルがLINE通知上で文字化けする障害が発生した
    (BeautifulSoupへ生バイト列を渡す際にエンコーディング自動判定が外れていた)。
    resp.content(bytes)からの復元がUTF-8として正しく行われることの回帰テスト。"""
    import config

    monkeypatch.setattr(config, "MFUJIN_BLOG_TOP_URL", "https://mfujin.test/")
    monkeypatch.setattr(config, "MFUJIN_BLOG_REQUEST_TIMEOUT_SEC", 5)
    session = _FakeSessionForFetch(TOP_PAGE_HTML.encode("utf-8"))

    summary = blog.fetch_latest_article_summary(session)

    assert summary.title == "ダミー記事タイトルA"


def test_fetch_article_content_decodes_utf8_bytes_correctly(monkeypatch):
    """上と同じ障害の回帰テスト(記事ページ取得側)。"""
    import config

    monkeypatch.setattr(config, "MFUJIN_BLOG_REQUEST_TIMEOUT_SEC", 5)
    session = _FakeSessionForFetch(_article_html().encode("utf-8"))

    article = blog.fetch_article_content(session, "https://mfujin.test/archives/dummy.html")

    assert article.title == "ダミー記事タイトル"
    assert article.category == "日常のひとこま"


def test_is_excluded_category_matches_known_pr_category(monkeypatch):
    import config

    monkeypatch.setattr(config, "MFUJIN_BLOG_EXCLUDED_CATEGORIES", ["PR記事", "お知らせ"])
    assert blog.is_excluded_category("PR記事") is True
    assert blog.is_excluded_category("日常のひとこま") is False
    # カテゴリが取得できない場合は除外しない(判定材料が無いため後続の画像枚数判定に委ねる)
    assert blog.is_excluded_category(None) is False


def test_build_line_messages_uses_text_and_images_within_limit():
    article = blog.ArticleContent(
        url="https://mfujin.test/archives/dummy.html",
        title="ダミー記事タイトル",
        published_at="2026-09-28T07:01:00+09:00",
        category="日常のひとこま",
        image_urls=["https://cdn.mfujin.test/imgs/page1.jpg", "https://cdn.mfujin.test/imgs/page2.jpg"],
    )
    messages = blog.build_line_messages(article)

    assert len(messages) == 3  # タイトル1通 + 画像2通 (5通上限以内)
    assert isinstance(messages[0], TextMessage)
    assert "ダミー記事タイトル" in messages[0].text
    assert "https://mfujin.test/archives/dummy.html" in messages[0].text
    assert isinstance(messages[1], ImageMessage)
    assert messages[1].original_content_url == "https://cdn.mfujin.test/imgs/page1.jpg"


def test_build_line_messages_switches_to_flex_carousel_when_over_limit():
    # タイトル1通 + 画像5枚 = 6通 > 5通上限 のため carousel に切り替わる
    image_urls = [f"https://cdn.mfujin.test/imgs/page{i}.jpg" for i in range(5)]
    article = blog.ArticleContent(
        url="https://mfujin.test/archives/dummy.html",
        title="ダミー記事タイトル",
        published_at="2026-09-28T07:01:00+09:00",
        category="日常のひとこま",
        image_urls=image_urls,
    )
    messages = blog.build_line_messages(article)

    assert len(messages) == 1
    assert isinstance(messages[0], FlexMessage)


class _FakeHeadResponse:
    def __init__(self, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


class _FakeSessionForValidation:
    def __init__(self, responses):
        self._responses = responses

    def head(self, url, timeout=None, allow_redirects=True):
        resp = self._responses[url]
        if isinstance(resp, Exception):
            raise resp
        return resp


def test_validate_image_urls_excludes_oversized_and_failed(monkeypatch):
    import config

    monkeypatch.setattr(config, "MFUJIN_BLOG_IMAGE_MAX_SIZE_MB", 1)
    ok_url = "https://cdn.mfujin.test/imgs/ok.jpg"
    too_big_url = "https://cdn.mfujin.test/imgs/too_big.jpg"
    broken_url = "https://cdn.mfujin.test/imgs/broken.jpg"

    session = _FakeSessionForValidation(
        {
            ok_url: _FakeHeadResponse(200, {"Content-Length": str(500 * 1024)}),
            too_big_url: _FakeHeadResponse(200, {"Content-Length": str(5 * 1024 * 1024)}),
            broken_url: _FakeHeadResponse(404),
        }
    )

    result = blog.validate_image_urls(session, [ok_url, too_big_url, broken_url])

    assert result == [ok_url]
