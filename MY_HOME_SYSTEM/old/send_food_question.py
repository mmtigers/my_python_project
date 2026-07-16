import sys
import re
import datetime
from typing import List, Tuple, Dict, Optional

# プロジェクト内モジュール
import config
import common
from linebot.v3.messaging import FlexMessage, FlexContainer

# ロガー設定
logger = common.setup_logging("food_question_v2")

# ==========================================
# UI設定 (ビジネスロジックと分離)
# ==========================================
UI_THEME = {
    "自炊": {
        "label": "🍳 自炊・家ご飯",
        "color": "#E67A7A",  # 暖色系
        "prefix": "🍳"
    },
    "外食": {
        "label": "🏪 外食・テイクアウト",
        "color": "#7AC2E6",  # 寒色系
        "prefix": "🏪"
    }
}

# 表示設定
RANKING_LIMIT = 3
LOOKBACK_DAYS = 30


def fetch_frequent_menus(days: int = 30) -> Dict[str, List[Tuple[str, int]]]:
    """
    DBから過去の履歴を集計し、カテゴリごとの頻出メニューを取得する。
    
    Returns:
        Dict[str, List[Tuple[str, int]]]
        例: {"自炊": [('カレー', 5), ...], "外食": [('マクドナルド', 3), ...]}
    """
    # 1. 結果格納用dictの初期化
    ranked_results = {cat: [] for cat in UI_THEME.keys()}

    # 検索対象の日付を計算 (YYYY-MM-DD)
    target_date = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y-%m-%d')

    try:
        # execute_read_query ではなく get_db_cursor を使用して直接Rowオブジェクトを取得する
        with common.get_db_cursor() as cursor:
            if not cursor:
                logger.warning("DBカーソルの取得に失敗しました。デフォルト値を使用します。")
                return ranked_results

            query = f"""
                SELECT menu_category, COUNT(*) as cnt 
                FROM {config.SQLITE_TABLE_FOOD} 
                WHERE timestamp > ?
                GROUP BY menu_category 
                ORDER BY cnt DESC
            """
            
            cursor.execute(query, (target_date,))
            rows = cursor.fetchall()
            
            if not rows:
                logger.info("直近の履歴データがありません。デフォルト値を使用します。")
                return ranked_results

            # 正規表現: "カテゴリ: メニュー (補足)" の形式を解析
            pattern = re.compile(r"^([^:]+):(.+)")

            for row in rows:
                content = row["menu_category"] # Rowオブジェクトなので辞書のようにアクセス可能
                if not content:
                    continue

                match = pattern.match(content)
                if match:
                    cat_raw = match.group(1).strip()
                    item_raw = match.group(2).strip()
                    
                    # サニタイズ: "(手入力)" などのシステム付与文字列を除去
                    item_clean = item_raw.replace("(手入力)", "").strip()
                    
                    # UI設定にあるカテゴリか判定 (部分一致許容: "自炊" in "自炊(その他)")
                    target_cat = next((key for key in UI_THEME.keys() if key in cat_raw), None)
                    
                    if target_cat and item_clean:
                        # 重複チェック (既存リストに名前がない場合のみ追加)
                        current_list = ranked_results[target_cat]
                        if item_clean not in [x[0] for x in current_list]:
                            current_list.append((item_clean, row["cnt"]))
                else:
                    logger.debug(f"Skipping malformed record: {content}")

    except Exception as e:
        logger.error(f"ランキングデータ取得エラー (Default fallback triggered): {e}", exc_info=True)

    return ranked_results


def fill_defaults_from_config(ranked_data: Dict[str, List[Tuple[str, int]]], limit: int) -> Dict[str, List[Tuple[str, int]]]:
    """
    データ不足分を config.MENU_OPTIONS の定義値で埋める。
    """
    for cat in ranked_data.keys():
        current_items = ranked_data[cat]
        current_names = {x[0] for x in current_items}
        
        # config.py からデフォルト候補を取得
        # configに定義がない場合は空リスト
        defaults = getattr(config, "MENU_OPTIONS", {}).get(cat, [])
        
        for d_item in defaults:
            if len(current_items) >= limit:
                break
            if d_item not in current_names:
                current_items.append((d_item, 0))
                
    return ranked_data


def create_food_flex_container(ranked_data: Dict[str, List[Tuple[str, int]]]) -> FlexContainer:
    """Flex Messageのコンテナを構築 (UI生成)"""
    
    body_contents = []
    
    for i, cat_key in enumerate(UI_THEME.keys()):
        theme = UI_THEME[cat_key]
        items = ranked_data.get(cat_key, [])
        
        # セクションヘッダー
        body_contents.append({
            "type": "text",
            "text": f"{theme['label']} (よく使う)",
            "size": "xs",
            "color": "#999999",
            "weight": "bold",
            "margin": "lg"
        })
        
        # ランキングボタン
        for item_name, _ in items[:RANKING_LIMIT]:
            display_label = (item_name[:18] + '..') if len(item_name) > 20 else item_name
            
            body_contents.append({
                "type": "button",
                "style": "secondary",
                "height": "sm",
                "action": {
                    "type": "postback",
                    "label": f"{theme['prefix']} {display_label}",
                    "data": f"action=food_record_direct&category={cat_key}&item={item_name}"
                },
                "margin": "xs"
            })
            
        # 「その他」手入力ボタン
        body_contents.append({
            "type": "button",
            "style": "link",
            "height": "sm",
            "action": {
                "type": "postback",
                "label": f"✏️ その他 ({cat_key})",
                "data": f"action=food_manual&category={cat_key}"
            },
            "margin": "none"
        })
        
        if i < len(UI_THEME) - 1:
             body_contents.append({"type": "separator", "margin": "md"})

    bubble = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "🍽️ 今日の夕食は？", "weight": "bold", "size": "xl", "color": "#FFFFFF"},
                {"type": "text", "text": "タップでかんたん記録", "size": "xs", "color": "#FFFFFFEE"}
            ],
            "backgroundColor": "#E67A7A",
            "paddingAll": "20px"
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": body_contents
        },
        "footer": {
            "type": "box",
            "layout": "horizontal",
            "contents": [
                 {
                    "type": "button",
                    "action": {"type": "postback", "label": "スキップ", "data": "action=food_skip"},
                    "color": "#AAAAAA"
                }
            ]
        }
    }
    return FlexContainer.from_dict(bubble)


def main():
    logger.info("--- 夕食アンケート処理開始 (v2.2 Stable) ---")
    
    try:
        # 1. データ取得
        raw_data = fetch_frequent_menus(days=LOOKBACK_DAYS)
        
        # 2. デフォルト値充填
        filled_data = fill_defaults_from_config(raw_data, limit=RANKING_LIMIT)
        
        # 3. Flex Message構築
        flex_content = create_food_flex_container(filled_data)
        msg = FlexMessage(altText="今日の夕食アンケートが届きました🍽️", contents=flex_content)
        
        # 4. 送信
        if common.send_push(config.LINE_USER_ID, [msg], target="line"):
            logger.info("送信完了✨")
        else:
            logger.error("送信失敗 (send_push returned False)")
            sys.exit(1)
            
    except Exception as e:
        logger.critical(f"予期せぬエラーで中断しました: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()