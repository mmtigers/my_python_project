# MY_HOME_SYSTEM/app_ranking_service.py
import sqlite3
import logging
import argparse
import requests
from datetime import datetime
from typing import List, Dict, Any, Optional
import pandas as pd

# 自作モジュール
import config
import common

# ロガー設定
logger = logging.getLogger('AppRankingService')

class AppRankingService:
    """
    アプリランキング情報を取得・保存・分析・通知するサービス
    ※安定性確保のため、Apple App Storeの公式RSSフィードを使用します。
    """
    
    TABLE_NAME: str = "app_rankings"
    FETCH_COUNT: int = 50  # 取得件数
    
    # Apple RSS Feed (JSON形式)
    URL_FREE: str = "https://rss.applemarketingtools.com/api/v2/jp/apps/top-free/50/apps.json"
    URL_PAID: str = "https://rss.applemarketingtools.com/api/v2/jp/apps/top-paid/50/apps.json"

    def __init__(self) -> None:
        self._ensure_table_exists()

    def _ensure_table_exists(self) -> None:
        """DBテーブルの初期化"""
        sql = f"""
        CREATE TABLE IF NOT EXISTS {self.TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            ranking_type TEXT, -- 'free' or 'paid'
            rank INTEGER,
            app_id TEXT,
            title TEXT,
            developer TEXT,
            icon_url TEXT,
            score REAL,
            recorded_at TEXT,
            UNIQUE(date, ranking_type, rank)
        )
        """
        # common.get_db_cursor を使用してリソース管理を委譲
        with common.get_db_cursor(commit=True) as cursor:
            if cursor:
                try:
                    cursor.execute(sql)
                except Exception as e:
                    self._handle_error(f"DB初期化エラー: {e}")

    def _handle_error(self, message: str) -> None:
        """エラーハンドリング共通処理"""
        logger.error(message)
        try:
            common.send_push(
                config.LINE_USER_ID, 
                [{"type": "text", "text": f"⚠️ アプリランキング エラー\n{message}"}], 
                target="discord", 
                channel="error"
            )
        except Exception:
            pass

    def fetch_and_save_rankings(self) -> None:
        """ランキングフィードからデータを取得してDBに保存"""
        today_str = datetime.now().strftime('%Y-%m-%d')
        logger.info(f"🚀 ランキング取得開始 (Source: Apple RSS): {today_str}")
        
        # 1. 無料ランキング
        self._fetch_rss(
            self.URL_FREE, 
            "free", 
            today_str
        )
        
        # 2. 有料ランキング
        self._fetch_rss(
            self.URL_PAID, 
            "paid", 
            today_str
        )
        
        logger.info("✅ 全処理完了")

    def _fetch_rss(self, url: str, type_label: str, today_str: str) -> None:
        """RSS(JSON)を取得してDBに保存"""
        try:
            logger.info(f"🌍 データ取得中: {type_label}...")
            
            res = requests.get(url, timeout=10)
            res.raise_for_status()
            
            data = res.json()
            # 型ヒントのためにキャスト (APIレスポンス構造の前提)
            feed: Dict[str, Any] = data.get('feed', {})
            results: List[Dict[str, Any]] = feed.get('results', [])
            
            apps: List[Dict[str, Any]] = []
            
            for item in results:
                try:
                    app_id = item.get('id')
                    title = item.get('name')
                    developer = item.get('artistName')
                    icon_url = item.get('artworkUrl100') # 100x100アイコン
                    
                    if not title or not app_id:
                        continue

                    apps.append({
                        "app_id": str(app_id),
                        "title": title,
                        "developer": developer,
                        "icon_url": icon_url,
                        "score": 0.0
                    })
                except Exception:
                    continue
            
            count = len(apps)
            logger.info(f"👉 取得件数: {count}件")

            if count == 0:
                logger.warning(f"データが見つかりませんでした ({type_label})")
                return

            # DB保存 (common経由)
            with common.get_db_cursor(commit=True) as cursor:
                if cursor:
                    for i, app in enumerate(apps):
                        rank = i + 1
                        sql = f"""
                        INSERT OR REPLACE INTO {self.TABLE_NAME}
                        (date, ranking_type, rank, app_id, title, developer, icon_url, score, recorded_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """
                        vals = (
                            today_str,
                            type_label,
                            rank,
                            app['app_id'],
                            app['title'],
                            app['developer'],
                            app['icon_url'],
                            app['score'],
                            common.get_now_iso()
                        )
                        cursor.execute(sql, vals)
            
            logger.info(f"💾 DB保存完了: {type_label}")

        except Exception as e:
            self._handle_error(f"RSS取得エラー ({type_label}): {e}")

    def analyze_and_notify(self, target: str = "discord") -> None:
        """前回との比較分析を行い通知する"""
        logger.info("📊 分析と通知処理を開始...")
        today = datetime.now()
        today_str = today.strftime('%Y-%m-%d')
        
        # common.get_db_cursor を使用して接続管理
        # Pandasには connection オブジェクトが必要なため cursor.connection を渡す
        with common.get_db_cursor() as cursor:
            if not cursor:
                return
            
            # 今日のデータ取得
            try:
                df_today = pd.read_sql_query(
                    f"SELECT * FROM {self.TABLE_NAME} WHERE date = '{today_str}'", 
                    cursor.connection
                )
            except Exception as e:
                logger.error(f"データ取得エラー(Today): {e}")
                return
            
            if df_today.empty:
                logger.warning("本日のデータがないため分析を中止します")
                return

            # 比較対象（過去の最新データ）を取得
            last_date_query = f"SELECT date FROM {self.TABLE_NAME} WHERE date < '{today_str}' ORDER BY date DESC LIMIT 1"
            try:
                cursor.execute(last_date_query)
                res = cursor.fetchone()
            except Exception as e:
                logger.error(f"データ取得エラー(Last Date): {e}")
                return
            
            if not res:
                logger.info("比較対象の過去データがありません（初回実行）")
                self._notify_first_time(df_today, target)
                return

            last_date_str: str = res[0]
            logger.info(f"比較対象日: {last_date_str}")
            
            try:
                df_last = pd.read_sql_query(
                    f"SELECT * FROM {self.TABLE_NAME} WHERE date = '{last_date_str}'", 
                    cursor.connection
                )
            except Exception as e:
                logger.error(f"データ取得エラー(Last Data): {e}")
                return
        
        # メッセージ生成
        message = self._generate_analysis_message(df_today, df_last, today_str, last_date_str)
        
        # 送信
        self._send_notification(message, target)

    def _generate_analysis_message(self, df_today: pd.DataFrame, df_last: pd.DataFrame, today_str: str, last_date_str: str) -> str:
        """分析ロジックとメッセージ生成（主婦向け）"""
        
        # --- 分析: 無料ランキング (free) ---
        df_today_free = df_today[df_today['ranking_type'] == 'free']
        df_last_free = df_last[df_last['ranking_type'] == 'free']
        
        # 1. NEW (新着)
        last_ids = df_last_free['app_id'].tolist()
        new_apps = df_today_free[~df_today_free['app_id'].isin(last_ids)].sort_values('rank').head(3)
        
        # 2. UP (急上昇)
        merged = pd.merge(df_today_free, df_last_free, on='app_id', suffixes=('', '_last'))
        # 型ヒントエラー回避のためのキャストや列操作はPandasの動的特性に任せるが、論理的には正しい
        merged['rank_diff'] = merged['rank_last'] - merged['rank'] # プラスなら上昇
        up_apps = merged.sort_values('rank_diff', ascending=False).head(3)
        up_apps = up_apps[up_apps['rank_diff'] >= 3] # 3ランク以上アップ

        # --- メッセージ構築 ---
        msg = f"📱 **今週のアプリ流行チェック**\n"
        msg += f"({last_date_str[5:]} との比較)\n\n"
        
        msg += "奥様、今週も一週間お疲れ様でした🍵\n"
        msg += "App Storeの最新ランキング情報をまとめました✨\n\n"

        if not new_apps.empty:
            msg += "**🆕 今週の初登場！**\n"
            for _, row in new_apps.iterrows():
                msg += f"・{row['rank']}位: **{row['title']}**\n"
            msg += "\n"
            
        if not up_apps.empty:
            msg += "**🔥 人気急上昇！**\n"
            for _, row in up_apps.iterrows():
                diff = int(row['rank_diff'])
                msg += f"・{row['title']} (⬆️{diff}UP)\n"
            msg += "\n"
        
        # トップ3
        msg += "**👑 今週の無料トップ3**\n"
        top3 = df_today_free.sort_values('rank').head(3)
        for _, row in top3.iterrows():
            medal = ['🥇','🥈','🥉'][row['rank']-1]
            msg += f"{medal} {row['title']}\n"
            
        msg += "\n詳細はダッシュボードの「🌟最近の流行」タブでご覧ください😊"
        
        return msg

    def _notify_first_time(self, df_today: pd.DataFrame, target: str) -> None:
        """初回実行時の通知"""
        df_free = df_today[df_today['ranking_type'] == 'free'].sort_values('rank').head(5)
        
        msg = "📱 **アプリ流行チェック (初回)**\n\n"
        msg += "奥様、アプリランキングの記録を開始しました✨\n"
        msg += "現在の「無料トップ5」はこちらです：\n\n"
        
        for _, row in df_free.iterrows():
            msg += f"{row['rank']}位: **{row['title']}**\n"
            
        msg += "\n来週からは、順位の変動をお知らせしますね！"
        self._send_notification(msg, target)

    def _send_notification(self, message: str, target: str) -> None:
        """通知送信共通処理"""
        targets = ['line', 'discord'] if target == 'both' else [target]
        
        for t in targets:
            try:
                common.send_push(config.LINE_USER_ID, [{"type": "text", "text": message}], target=t)
                logger.info(f"送信完了 ({t})")
            except Exception as e:
                logger.error(f"送信失敗 ({t}): {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', type=str, default='fetch', choices=['fetch', 'analyze'], help='実行モード')
    parser.add_argument('--target', type=str, default='discord', help='通知先')
    args = parser.parse_args()
    
    service = AppRankingService()
    
    if args.mode == 'fetch':
        service.fetch_and_save_rankings()
        # 金曜日(4)なら分析も行う
        if datetime.now().weekday() == 4:
            service.analyze_and_notify(target=args.target)
    
    elif args.mode == 'analyze':
        service.analyze_and_notify(target=args.target)