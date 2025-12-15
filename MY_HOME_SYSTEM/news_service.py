import feedparser
import logging

# ログ設定
logger = logging.getLogger('NewsService')

class NewsService:
    """
    Yahoo!ニュースのRSSから最新トピックス（タイトルとURL）を取得するクラス
    """
    
    # Yahooニュース・トピックス（主要）
    RSS_URL = "https://news.yahoo.co.jp/rss/topics/top-picks.xml"

    def get_top_news(self, limit=5) -> list:
        """
        最新ニュースのリストを返す
        
        Returns:
            list: [{'title': '記事タイトル', 'link': '記事URL'}, ...] の形式
        """
        try:
            feed = feedparser.parse(self.RSS_URL)
            
            if not feed.entries:
                logger.warning("⚠️ ニュースRSSの取得に失敗、または記事がありません。")
                return []

            news_list = []
            for entry in feed.entries[:limit]:
                # タイトルとリンクを辞書として格納
                news_item = {
                    "title": entry.title,
                    "link": entry.link
                }
                news_list.append(news_item)
            
            return news_list

        except Exception as e:
            logger.error(f"❌ ニュース取得中にエラーが発生しました: {e}")
            return []

if __name__ == "__main__":
    # テスト実行
    logging.basicConfig(level=logging.INFO)
    service = NewsService()
    news = service.get_top_news()
    
    if news:
        print(f"✅ 取得成功 ({len(news)}件):")
        for n in news:
            print(f"- {n['title']}")
            print(f"  └ {n['link']}")
    else:
        print("❌ 取得失敗")