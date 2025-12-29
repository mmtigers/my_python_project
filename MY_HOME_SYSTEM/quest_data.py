"""
Family Quest Master Data
ユーザー、クエスト、報酬の定義ファイルです。
ここを編集してサーバーを再起動（または同期APIを実行）すると、アプリに反映されます。
"""

# ユーザー定義
# ※ level, exp, gold は「新規登録時」の初期値です。既存ユーザーのデータはリセットされません。
USERS = [
    {'user_id': 'dad', 'name': 'まさひろ', 'job_class': '勇者', 'level': 1, 'exp': 0, 'gold': 50},
    {'user_id': 'mom', 'name': 'はるな', 'job_class': '魔法使い', 'level': 1, 'exp': 0, 'gold': 150},
    {'user_id': 'sun', 'name': 'ともや', 'job_class': '遊び人', 'level': 1, 'exp': 0, 'gold': 0},
    # {'user_id': 'daughter', 'name': 'すずか', 'job_class': '魔法使い', 'level': 1, 'exp': 0, 'gold': 150}
]

# クエスト定義
# type: 'daily' (毎日) or 'weekly' (週間)
# days: 曜日指定 (0=月, 1=火, ... 6=日)。毎日なら None
QUESTS = [
    # --- デイリー (共通) ---
    {'id': 1, 'title': 'お風呂掃除', 'type': 'daily', 'target': 'all', 'exp': 10, 'gold': 10, 'icon': '💧', 'days': None},
    {'id': 2, 'title': '食器洗い', 'type': 'daily', 'target': 'all', 'exp': 15, 'gold': 5, 'icon': '🍽️', 'days': None},
    
    # --- デイリー (個人) ---
    {'id': 8, 'title': '保育園送り', 'type': 'daily', 'target': 'dad', 'exp': 25, 'gold': 10, 'icon': '🚲', 'days': '1,2,3,4,5'},
    {'id': 30, 'title': 'お花の水やり', 'type': 'daily', 'target': 'mom', 'exp': 10, 'gold': 5, 'icon': '🌻', 'days': '0,2,4,6'},
    
    # --- 期間限定 (イベント) ---
    {'id': 100, 'title': '【年末】大掃除：窓拭き', 'type': 'limited', 'target': 'all', 'exp': 100, 'gold': 50, 'icon': '🪟', 'start': '2025-12-25', 'end': '2025-12-31'},
    
    # --- ランダム出現 (低確率・高報酬) ---
    {'id': 200, 'title': 'はぐれメタルの討伐(家中のゴミ拾い)', 'type': 'random', 'target': 'all', 'exp': 500, 'gold': 100, 'icon': '🔘', 'chance': 0.1},
    {'id': 201, 'title': 'パパへの肩たたき券発行', 'type': 'random', 'target': 'sun', 'exp': 50, 'gold': 30, 'icon': '💆', 'chance': 0.3},
]

# 報酬アイテム定義
# category: 'food', 'service', 'equip', 'special'
REWARDS = [
    {'id': 101, 'title': '高級アイス', 'category': 'food', 'cost': 100, 'icon': '🍨'},
    {'id': 102, 'title': 'ビール/お酒', 'category': 'food', 'cost': 150, 'icon': '🍺'},
    {'id': 103, 'title': 'マッサージ券', 'category': 'service', 'cost': 500, 'icon': '💆'},
    {'id': 201, 'title': 'はやての靴', 'category': 'equip', 'cost': 3000, 'icon': '👟'},
    {'id': 202, 'title': '勇者のゲーム', 'category': 'equip', 'cost': 5000, 'icon': '🎮'},
    {'id': 203, 'title': '時の砂時計', 'category': 'special', 'cost': 1000, 'icon': '⏳'},
    {'id': 204, 'title': '伝説の包丁', 'category': 'equip', 'cost': 2500, 'icon': '🔪'},
]