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
    {'id': 1, 'title': 'お風呂掃除', 'type': 'daily', 'exp': 10, 'gold': 10, 'icon': '💧', 'days': None},
    {'id': 2, 'title': '食器洗い', 'type': 'daily', 'exp': 15, 'gold': 5, 'icon': '🍽️', 'days': None},
    {'id': 3, 'title': '洗濯干し', 'type': 'daily', 'exp': 15, 'gold': 5, 'icon': '👕', 'days': None},
    {'id': 4, 'title': '燃えるゴミ出し', 'type': 'weekly', 'exp': 30, 'gold': 15, 'icon': '🔥', 'days': '1,4'}, # 火・金
    {'id': 5, 'title': 'プラゴミ出し', 'type': 'weekly', 'exp': 30, 'gold': 15, 'icon': '♻️', 'days': '3'},   # 木
    {'id': 6, 'title': '週末の買い出し', 'type': 'weekly', 'exp': 50, 'gold': 30, 'icon': '🛒', 'days': '0,6'}, # 月・日
    {'id': 7, 'title': '寝かしつけ', 'type': 'daily', 'exp': 40, 'gold': 0, 'icon': '💤', 'days': None},
    {'id': 8, 'title': '保育園送り', 'type': 'daily', 'exp': 25, 'gold': 10, 'icon': '🚲', 'days': '1,2,3,4,5'},
    # 必要に応じて以下のように追加してください
    # {'id': 9, 'title': '部屋の片付け', 'type': 'daily', 'exp': 10, 'gold': 5, 'icon': '🧹', 'days': None},
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