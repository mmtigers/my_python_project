import feedparser
import logging
import requests
import common

logger = logging.getLogger('NewsService')

class NewsService:
    # Google News RSS (検索クエリ指定)
    # hl=ja&gl=JP&ceid=JP:ja で日本語・日本向けを指定
    RSS_HYOGO_ITAMI = "https://news.google.com/rss/search?q=兵庫県伊丹市&hl=ja&gl=JP&ceid=JP:ja"
    RSS_NARA = "https://news.google.com/rss/search?q=奈良県&hl=ja&gl=JP&ceid=JP:ja"
    RSS_TOP = "https://news.google.com/rss?hl=ja&gl=JP&ceid=JP:ja"
    
    # ブラウザのふりをするヘッダー (アクセス拒否回避)
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    def _fetch_feed(self, url):
        """RSSを安全に取得"""
        try:
            session = common.get_retry_session()
            res = session.get(url, headers=self.HEADERS, timeout=10)
            if res.status_code == 200:
                return feedparser.parse(res.content)
            else:
                logger.warning(f"RSS取得失敗: {url} (Status: {res.status_code})")
                return None
        except Exception as e:
            logger.error(f"RSS通信エラー: {e}")
            return None

    def get_local_news(self, limit=3) -> list:
        """兵庫・伊丹と奈良のローカルニュースを取得"""
        news_list = []
        
        # 1. 兵庫(伊丹)
        feed_h = self._fetch_feed(self.RSS_HYOGO_ITAMI)
        if feed_h and feed_h.entries:
            for entry in feed_h.entries[:2]:
                news_list.append({"title": f"[伊丹/兵庫] {entry.title}", "link": entry.link})
        
        # 2. 奈良
        feed_n = self._fetch_feed(self.RSS_NARA)
        if feed_n and feed_n.entries:
            for entry in feed_n.entries[:2]:
                news_list.append({"title": f"[奈良] {entry.title}", "link": entry.link})
            
        # 混ぜて返す
        return news_list[:limit]

    def get_top_news(self, limit=3) -> list:
        """全国トップニュース"""
        feed = self._fetch_feed(self.RSS_TOP)
        if feed and feed.entries:
            return [{"title": e.title, "link": e.link} for e in feed.entries[:limit]]
        return []

if __name__ == "__main__":
    # テスト実行
    logging.basicConfig(level=logging.INFO)
    service = NewsService()
    
    print("📰 --- ローカルニュース ---")
    local = service.get_local_news()
    for n in local:
        print(f"- {n['title']}")
        
    print("\n📰 --- トップニュース ---")
    top = service.get_top_news()
    for n in top:
        print(f"- {n['title']}")