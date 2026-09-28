# MY_HOME_SYSTEM/monitors/mfujin_blog_monitor.py
"""
「コミックエッセイ えむふじんがあらわれた」(https://mfujin.blog.jp/)の最新記事を巡回し、
漫画記事と判定できた場合のみ本文中の漫画画像をLINEへ通知する個人用バッチ。

cronから1日数回(12:00頃の本実行・取りこぼし救済の15:00頃再実行)起動する想定
(deploy/cron/crontab参照)。同一記事の重複送信は`mfujin_blog_notifications`テーブルの
article_url UNIQUE制約で防止する。LINE送信が失敗した記事はstatus='failed'で記録され、
次回実行時に再送を試みる(成功扱いへの誤記録を避けるため、DB更新はLINE送信の成否が
確定した後にのみ行う)。

HTML解析・LINEメッセージ組み立ての実装は services/mfujin_blog_service.py に集約している。
"""
import dataclasses
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import config
import requests
from core.database import get_db_cursor
from core.logger import setup_logging
from core.utils import get_now_iso
from services import mfujin_blog_service as blog
from services.notification_service import send_push

logger = setup_logging("mfujin_blog_monitor")


def _get_existing_status(article_url: str) -> str | None:
    with get_db_cursor() as cur:
        cur.execute(
            f"SELECT status FROM {config.SQLITE_TABLE_MFUJIN_NOTIFICATIONS} WHERE article_url = ?",  # nosec B608
            (article_url,),
        )
        row = cur.fetchone()
    return row["status"] if row else None


def _record(
    article_url: str,
    title: str,
    published_at: str | None,
    status: str,
    image_count: int,
    *,
    error_detail: str | None = None,
    sent: bool = False,
) -> None:
    now = get_now_iso()
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            f"""
            INSERT INTO {config.SQLITE_TABLE_MFUJIN_NOTIFICATIONS}
                (article_url, article_title, published_at, fetched_at, sent_at, status, image_count, error_detail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(article_url) DO UPDATE SET
                article_title = excluded.article_title,
                published_at = excluded.published_at,
                fetched_at = excluded.fetched_at,
                sent_at = excluded.sent_at,
                status = excluded.status,
                image_count = excluded.image_count,
                error_detail = excluded.error_detail
            """,  # nosec B608
            (
                article_url,
                title,
                published_at,
                now,
                now if sent else None,
                status,
                image_count,
                error_detail,
            ),
        )


def _notify_operational_error(message: str) -> None:
    """サイト構造の変化・通信エラー等の運用異常。個人のLINEではなくDiscordのエラー
    チャンネルへ通知する(既存のnotification_serviceの慣習に合わせる)。"""
    logger.error(message)
    send_push(
        messages=[{"type": "text", "text": f"⚠️ えむふじん監視エラー: {message}"}],
        target="discord",
        channel="error",
    )


def run() -> None:
    if not config.MFUJIN_BLOG_ENABLED:
        logger.info("MFUJIN_BLOG_ENABLED=false のため終了します")
        return

    session = blog.build_session()

    try:
        summary = blog.fetch_latest_article_summary(session)
    except (requests.RequestException, blog.MfujinBlogError) as e:
        _notify_operational_error(f"最新記事一覧の取得に失敗しました: {e}")
        return

    existing_status = _get_existing_status(summary.url)
    if existing_status is not None and existing_status != "failed":
        logger.info(f"既に処理済みのためスキップします (url={summary.url}, status={existing_status})")
        return

    try:
        article = blog.fetch_article_content(session, summary.url)
    except (requests.RequestException, blog.MfujinBlogError) as e:
        _notify_operational_error(f"記事本文の取得に失敗しました (url={summary.url}): {e}")
        return

    if blog.is_excluded_category(article.category):
        logger.info(f"除外カテゴリのためスキップします (url={article.url}, category={article.category})")
        _record(article.url, article.title, article.published_at, "skipped_category", 0)
        return

    if len(article.image_urls) < config.MFUJIN_BLOG_MIN_IMAGE_COUNT:
        _notify_operational_error(
            f"本編の漫画画像が{len(article.image_urls)}枚でした(閾値{config.MFUJIN_BLOG_MIN_IMAGE_COUNT}枚未満)。"
            f"HTML構造が変わった可能性があります (url={article.url}, category={article.category})"
        )
        _record(article.url, article.title, article.published_at, "skipped_no_images", len(article.image_urls))
        return

    validated_images = blog.validate_image_urls(session, article.image_urls)
    if not validated_images:
        _notify_operational_error(f"抽出した画像がすべて検証に失敗しました (url={article.url})")
        _record(article.url, article.title, article.published_at, "skipped_no_images", 0)
        return

    messages = blog.build_line_messages(dataclasses.replace(article, image_urls=validated_images))
    ok = send_push(messages=messages, target="line", user_id=config.MFUJIN_BLOG_LINE_USER_ID)

    if not ok:
        _record(
            article.url,
            article.title,
            article.published_at,
            "failed",
            len(validated_images),
            error_detail="LINE送信に失敗しました",
        )
        logger.error(f"LINE送信に失敗しました: {article.url}")
        return

    _record(article.url, article.title, article.published_at, "sent", len(validated_images), sent=True)
    logger.info(f"送信完了: {article.title} ({len(validated_images)}枚)")


if __name__ == "__main__":
    run()
