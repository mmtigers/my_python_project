-- えむふじんブログ(https://mfujin.blog.jp/)の最新記事監視・LINE通知機能の
-- 重複送信防止テーブル。article_url の UNIQUE制約で同一記事の二重送信を構造的に防ぐ。
-- status: sent / failed / skipped_category / skipped_no_images
CREATE TABLE IF NOT EXISTS mfujin_blog_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_url TEXT NOT NULL UNIQUE,
    article_title TEXT NOT NULL,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    sent_at TEXT,
    status TEXT NOT NULL,
    image_count INTEGER NOT NULL DEFAULT 0,
    error_detail TEXT
);
