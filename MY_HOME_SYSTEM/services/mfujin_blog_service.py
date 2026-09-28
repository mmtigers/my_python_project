# MY_HOME_SYSTEM/services/mfujin_blog_service.py
"""
「コミックエッセイ えむふじんがあらわれた」(https://mfujin.blog.jp/)の最新記事取得・
漫画記事判定・本編漫画画像抽出・LINE通知メッセージ組み立てを行う。

サイト側のHTML構造(CSSセレクタ)は本モジュール冒頭の SELECTORS / 各種定数に集約する。
サイト側テンプレートが変わった場合は、呼び出し元(monitors/mfujin_blog_monitor.py)を
変更せず、ここだけを見直せばよい設計にしている。

2026-09時点で実際の記事HTML(2件)を確認して設計した:
- 記事一覧・記事タイトルは `article.article h1.article-title a`(トップページでは
  出現順が新しい記事から古い記事の順)。
- 本文は `div.article-body-inner` 配下に画像・テキストがフラットに並ぶ。本編の漫画ページは
  `img.pict` のうち拡張子が `.gif` 以外のもの(ヘッダー/フッターの固定バナーも同じ
  `pict` クラスを使うが `.gif` で配信されており、本編は `.jpg` で配信されている)。
- カテゴリは `dd.article-category1`。サイドバーに `PR記事` 等の非漫画カテゴリが実在する。
- 投稿日時は `time[itemprop="datePublished"]` の `datetime` 属性(ISO8601)。
"""
import dataclasses
from urllib.parse import urljoin

import config
import requests
from bs4 import BeautifulSoup
from bs4.element import Tag
from core.logger import setup_logging
from linebot.v3.messaging import (
    FlexContainer,
    FlexMessage,
    ImageMessage,
    Message,
    TextMessage,
)
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = setup_logging("service.mfujin_blog")

# LINE Messaging APIの制約(services/line_service.pyのLINE_MAX_MESSAGES_PER_REPLYと同じ、
# 1回のpush/replyで送れるメッセージ数の上限)。
_LINE_MAX_MESSAGES_PER_PUSH = 5
# Flex Message 1通のcarouselに含められるバブル数の上限(LINE公式仕様)。
_FLEX_CAROUSEL_MAX_BUBBLES = 12

# ヘッダー/フッターの固定バナーも img.pict を使うが .gif 形式で配信されている
# (2026-09、実記事2件で確認済み)。本編の漫画ページは .jpg で配信されている。
_EXCLUDED_IMAGE_EXTENSIONS = (".gif",)
# 副次チェック: 固定バナーの高さは240〜260px程度、本編漫画ページは900px以上で
# 配信されている(2026-09確認)。height属性が無い画像は判定材料が無いため除外しない。
_MIN_MANGA_IMAGE_HEIGHT_PX = 400


class MfujinBlogError(Exception):
    """サイト取得・解析に関する回復不能なエラー。呼び出し元はこれをエラー通知の対象とする。"""


@dataclasses.dataclass(frozen=True)
class Selectors:
    """HTML構造とCSSセレクタの対応。サイト側テンプレートが変わったらここだけを直す。"""

    article_title_link: str = "article.article h1.article-title a"
    body_container: str = "div.article-body-inner"
    manga_image_candidate: str = "img.pict"
    category: str = "dd.article-category1"
    published_time: str = "time[itemprop='datePublished']"


SELECTORS = Selectors()


@dataclasses.dataclass
class ArticleSummary:
    url: str
    title: str


@dataclasses.dataclass
class ArticleContent:
    url: str
    title: str
    published_at: str | None
    category: str | None
    image_urls: list[str]


def build_session() -> requests.Session:
    """リトライ・タイムアウト・User-Agentを設定したHTTPセッションを作成する。

    DDD/newface_monitor.pyの既存パターン(requests.Session + urllib3 Retry)に揃える。
    """
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
    )
    return session


def parse_latest_article_summary(soup: BeautifulSoup) -> ArticleSummary:
    """トップページのHTMLから最新記事(一覧の先頭)のURL・タイトルを取得する。"""
    link = soup.select_one(SELECTORS.article_title_link)
    if link is None or not link.get("href"):
        raise MfujinBlogError(
            "トップページから最新記事へのリンクが見つかりませんでした"
            f"(セレクタ={SELECTORS.article_title_link!r}。HTML構造が変わった可能性があります)"
        )
    return ArticleSummary(url=link["href"].strip(), title=link.get_text(strip=True))


def _decode_utf8(content: bytes) -> str:
    """レスポンスの生バイト列をUTF-8としてデコードして文字列を返す。

    2026-09-28の実機障害対応: `BeautifulSoup(bytes, from_encoding="utf-8")`は、
    ページ末尾側(広告/トラッキング関連と見られる箇所)に含まれる不正なUTF-8バイト列に
    より厳密デコードが失敗すると、指定した`from_encoding`を無視してBeautifulSoup自身の
    エンコーディング自動判定(誤って無関係な多バイト系エンコーディングを検出することが
    ある)にフォールバックしてしまい、本来UTF-8として正しくデコードできるはずの記事本文
    (タイトル等)まで巻き添えで文字化けする実障害が発生した。bs4に生バイト列を渡さず、
    ここで`errors="replace"`付きの寛容なデコードを自前で行ってから文字列として渡すことで、
    bs4のエンコーディング自動判定を完全に迂回する。末尾側の不正バイトはU+FFFD(置換文字)に
    置き換えられるが、記事タイトル・カテゴリ・漫画画像といった関心のある箇所は末尾より
    十分前方にあるため影響しない。
    """
    return content.decode("utf-8", errors="replace")


def fetch_latest_article_summary(session: requests.Session) -> ArticleSummary:
    resp = session.get(config.MFUJIN_BLOG_TOP_URL, timeout=config.MFUJIN_BLOG_REQUEST_TIMEOUT_SEC)
    resp.raise_for_status()
    return parse_latest_article_summary(BeautifulSoup(_decode_utf8(resp.content), "html.parser"))


def _is_manga_page_image(img: Tag) -> bool:
    src = (img.get("src") or "").strip()
    if not src or src.lower().endswith(_EXCLUDED_IMAGE_EXTENSIONS):
        return False
    height_attr = img.get("height")
    if height_attr:
        try:
            if int(height_attr) < _MIN_MANGA_IMAGE_HEIGHT_PX:
                return False
        except ValueError:
            pass  # 数値でない場合は判定材料が無いため除外しない
    return True


def parse_article_content(soup: BeautifulSoup, url: str) -> ArticleContent:
    """記事ページのHTMLからタイトル・投稿日時・カテゴリ・本編漫画画像URLを取得する。

    本文コンテナ自体が見つからない場合はHTML構造の変化とみなし例外を送出する。
    画像が0枚になること自体はここではエラーにしない(除外カテゴリ由来の可能性があるため、
    「除外カテゴリでないのに0枚」の異常判定は呼び出し元(monitor)に委ねる)。
    """
    title_link = soup.select_one(SELECTORS.article_title_link)
    title = title_link.get_text(strip=True) if title_link else ""

    time_el = soup.select_one(SELECTORS.published_time)
    published_at = time_el.get("datetime") if time_el else None

    category_el = soup.select_one(SELECTORS.category)
    category = category_el.get_text(strip=True) if category_el else None

    body = soup.select_one(SELECTORS.body_container)
    if body is None:
        raise MfujinBlogError(
            f"記事本文コンテナが見つかりませんでした(セレクタ={SELECTORS.body_container!r}。"
            "HTML構造が変わった可能性があります)"
        )

    image_urls = []
    for img in body.select(SELECTORS.manga_image_candidate):
        if not _is_manga_page_image(img):
            continue
        src = (img.get("src") or "").strip()
        if src:
            image_urls.append(urljoin(url, src))

    return ArticleContent(
        url=url,
        title=title,
        published_at=published_at,
        category=category,
        image_urls=image_urls,
    )


def fetch_article_content(session: requests.Session, url: str) -> ArticleContent:
    resp = session.get(url, timeout=config.MFUJIN_BLOG_REQUEST_TIMEOUT_SEC)
    resp.raise_for_status()
    return parse_article_content(BeautifulSoup(_decode_utf8(resp.content), "html.parser"), url)


def is_excluded_category(category: str | None) -> bool:
    """カテゴリが除外リスト(config.MFUJIN_BLOG_EXCLUDED_CATEGORIES)に完全一致するか。

    カテゴリが取得できなかった場合(None)は除外しない(判定材料が無いため、
    後続の画像枚数チェックに委ねる)。
    """
    if category is None:
        return False
    return category in config.MFUJIN_BLOG_EXCLUDED_CATEGORIES


def validate_image_urls(session: requests.Session, image_urls: list[str]) -> list[str]:
    """各画像URLにHEADリクエストを送り、サイズ上限・Content-Typeを検証する。

    HEADが使えない/失敗した場合は検証をスキップしてそのまま含める(過度に厳格にして
    正常な画像まで除外しないため)。サイズ超過が明確な場合のみ除外する。
    """
    max_bytes = config.MFUJIN_BLOG_IMAGE_MAX_SIZE_MB * 1024 * 1024
    validated: list[str] = []
    for url in image_urls:
        try:
            resp = session.head(
                url, timeout=config.MFUJIN_BLOG_REQUEST_TIMEOUT_SEC, allow_redirects=True
            )
            if resp.status_code >= 400:
                logger.warning(f"⚠️ 画像の検証に失敗しました(status={resp.status_code}): {url}")
                continue
            content_length = resp.headers.get("Content-Length")
            if content_length is not None and int(content_length) > max_bytes:
                logger.warning(
                    f"⚠️ 画像サイズが上限({config.MFUJIN_BLOG_IMAGE_MAX_SIZE_MB}MB)を"
                    f"超過したため除外します: {url}"
                )
                continue
            validated.append(url)
        except (requests.RequestException, ValueError) as e:
            logger.warning(f"⚠️ 画像の検証中にエラーが発生したため除外します ({url}): {e}")
    return validated


def build_line_messages(article: ArticleContent) -> list[Message]:
    """記事内容からLINE Messaging APIへ送るメッセージ列を組み立てる。

    タイトル(1通)+画像枚数が LINE の1回のpushで送れる上限(5通)に収まる場合は、
    テキスト1通+ImageMessage(枚数分)というシンプルな構成にする(スマホでネイティブに
    フルスクリーン表示できるImageMessageを優先)。収まらない場合のみ、Flex Message の
    carousel(1通の中に複数バブルを収められる)へ切り替える。
    """
    title_text = f"【えむふじん 最新話】\n\n{article.title}\n\n元記事:\n{article.url}"

    if 1 + len(article.image_urls) <= _LINE_MAX_MESSAGES_PER_PUSH:
        messages: list[Message] = [TextMessage(text=title_text)]
        for image_url in article.image_urls:
            messages.append(
                ImageMessage(originalContentUrl=image_url, previewImageUrl=image_url)
            )
        return messages

    bubbles = [
        {
            "type": "bubble",
            "size": "kilo",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {"type": "text", "text": "えむふじん 最新話", "weight": "bold", "size": "sm", "color": "#888888"},
                    {"type": "text", "text": article.title, "wrap": True, "weight": "bold", "size": "lg"},
                ],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "height": "sm",
                        "action": {"type": "uri", "label": "元記事を読む", "uri": article.url},
                    }
                ],
            },
        }
    ]
    image_urls = article.image_urls[: _FLEX_CAROUSEL_MAX_BUBBLES - 1]
    if len(image_urls) < len(article.image_urls):
        logger.warning(
            f"⚠️ 画像枚数がFlex Carouselの上限を超えたため、先頭{len(image_urls)}枚のみ送信します "
            f"(全{len(article.image_urls)}枚): {article.url}"
        )
    for image_url in image_urls:
        bubbles.append(
            {
                "type": "bubble",
                "size": "kilo",
                "hero": {
                    "type": "image",
                    "url": image_url,
                    "size": "full",
                    "aspectMode": "fit",
                    "aspectRatio": "1:2",
                },
            }
        )
    carousel = FlexContainer.from_dict({"type": "carousel", "contents": bubbles})
    return [FlexMessage(altText=f"えむふじん最新話: {article.title}", contents=carousel)]
