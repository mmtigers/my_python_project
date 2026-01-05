"""
Family Quest Master Data - Phase 2 Expansion
[2026-01-05 更新]
- ママのお手伝い、トイレ掃除、ルンバ整備などを追加
- ご褒美（Youtube、温泉、旅行など）を大幅拡充
"""

# ==========================================
# 1. ユーザー定義
# ==========================================
USERS = [
    {
        'user_id': 'dad', 'name': 'まさひろ', 'job_class': '会社員', 
        'level': 1, 'exp': 0, 'gold': 0, 'avatar': '⚔️',
        'info': '35歳 / INTJ / 三菱電機勤務 / 186cm' 
    },
    {
        'user_id': 'mom', 'name': 'はるな', 'job_class': '専業主婦', 
        'level': 1, 'exp': 0, 'gold': 0, 'avatar': '🪄',
        'info': '32歳 / 育児・家庭運営責任者' 
    },
    {
        'user_id': 'son', 'name': 'ともや', 'job_class': '年長', 
        'level': 1, 'exp': 0, 'gold': 0, 'avatar': '👦',
        'info': '5歳 / 学習習慣形成フェーズ' 
    },
    {
        'user_id': 'daughter', 'name': 'すずか', 'job_class': '遊び人', 
        'level': 1, 'exp': 0, 'gold': 0, 'avatar': '👶',
        'info': '2歳 / 生活習慣学習フェーズ' 
    }
]

# ==========================================
# 2. クエスト定義
# ==========================================
QUESTS = [
    # --- 共通: 基本生活習慣 (朝) ---
    {'id': 1, 'title': 'お着替え (準備含む)', 'type': 'daily', 'target': 'all', 'exp': 20, 'gold': 5, 'icon': '👕', 'start_time': '05:00', 'end_time': '08:00'},
    {'id': 2, 'title': 'はみがき (朝)', 'type': 'daily', 'target': 'all', 'exp': 15, 'gold': 5, 'icon': '🪥', 'start_time': '05:00', 'end_time': '09:00'},
    {'id': 901, 'title': 'お皿洗い', 'type': 'infinite', 'target': 'all', 'exp': 15, 'gold': 50, 'icon': '🍽️', 'desc': 'ご飯のあとのお皿をきれいに洗おう（何度でもOK）', 'chance': 1.0},
    
    # --- 共通: 協力・お手伝い (新規追加) ---
    # 土曜日(6)限定
    {'id': 7, 'title': 'ルンバの水交換', 'type': 'daily', 'target': 'all', 'exp': 50, 'gold': 30, 'icon': '🤖', 'days': '6'},
    # 毎日
    {'id': 8, 'title': '寝る前のおもちゃ片付け', 'type': 'daily', 'target': 'all', 'exp': 40, 'gold': 20, 'icon': '🧸', 'start_time': '19:00', 'end_time': '21:00'},

    # --- 智矢 (Son) ---
    {'id': 40, 'title': '朝のトイレに行く', 'type': 'daily', 'target': 'son', 'exp': 10, 'gold': 5, 'icon': '🚽', 'start_time': '05:00', 'end_time': '07:30'},
    {'id': 41, 'title': '寝る前のトイレに行く', 'type': 'daily', 'target': 'son', 'exp': 10, 'gold': 5, 'icon': '🚽', 'start_time': '19:00', 'end_time': '20:30'},
    {'id': 42, 'title': '朝起きたら顔を洗う', 'type': 'daily', 'target': 'son', 'exp': 10, 'gold': 5, 'icon': '🧖', 'start_time': '05:00', 'end_time': '08:00'},
    {'id': 43, 'title': '一人で本を読む', 'type': 'daily', 'target': 'son', 'exp': 30, 'gold': 10, 'icon': '📖'},
    {'id': 44, 'title': '靴を並べるお手伝い', 'type': 'daily', 'target': 'son', 'exp': 20, 'gold': 10, 'icon': '👞'},
    {'id': 45, 'title': 'ピアノの練習', 'type': 'daily', 'target': 'son', 'exp': 50, 'gold': 20, 'icon': '🎹'},
    # 新規追加: ママのお手伝い
    {'id': 48, 'title': 'ママのお手伝い', 'type': 'infinite', 'target': 'son', 'exp': 30, 'gold': 10, 'icon': '🧚', 'desc': 'ママに頼まれたことをやろう（何度でもOK）'},
    
    # 土日限定 (0=Sun, 6=Sat)
    {'id': 46, 'title': '休みの日は買い物についてくる', 'type': 'daily', 'target': 'son', 'exp': 100, 'gold': 50, 'icon': '🛒', 'days': '0,6'},
    # ボーナス (高報酬)
    {'id': 47, 'title': '朝起きておねしょをしていない', 'type': 'daily', 'target': 'son', 'exp': 100, 'gold': 50, 'icon': '✨'},
    
    # 既存: 勉強
    {'id': 30, 'title': '国語プリント完了', 'type': 'daily', 'target': 'son', 'exp': 50, 'gold': 20, 'icon': '📝'},
    {'id': 31, 'title': '算数プリント完了', 'type': 'daily', 'target': 'son', 'exp': 50, 'gold': 20, 'icon': '🧮'},
    {'id': 3, 'title': '朝ごはんを食べる (完食)', 'type': 'daily', 'target': 'son', 'exp': 20, 'gold': 5, 'icon': '🍳', 'start_time': '05:00', 'end_time': '09:00'},

    # --- 涼花 (Daughter) ---
    {'id': 301, 'title': '朝ごはんを食べる (完食)', 'type': 'daily', 'target': 'daughter', 'exp': 20, 'gold': 5, 'icon': '🍳', 'start_time': '05:00', 'end_time': '09:00'},

    # --- 共通: 基本生活習慣 (夜) ---
    {'id': 4, 'title': 'はみがき (夜)', 'type': 'daily', 'target': 'all', 'exp': 15, 'gold': 5, 'icon': '🪥', 'start_time': '17:00', 'end_time': '22:00'},
    {'id': 5, 'title': 'お風呂にはいる', 'type': 'daily', 'target': 'all', 'exp': 20, 'gold': 10, 'icon': '🛁', 'start_time': '17:00', 'end_time': '22:00'},
    {'id': 6, 'title': '明日の準備', 'type': 'daily', 'target': 'son', 'exp': 15, 'gold': 5, 'icon': '🎒', 'start_time': '17:00', 'end_time': '22:00'},

    # --- 父 (Dad) ---
    {'id': 10, 'title': '会社勤務 (通常)', 'type': 'daily', 'target': 'dad', 'exp': 200, 'gold': 50, 'icon': '🏢', 'days': '1,2,3,4,5'},
    {'id': 11, 'title': '会社勤務 (高負荷・残業)', 'type': 'daily', 'target': 'dad', 'exp': 350, 'gold': 80, 'icon': '🔥', 'days': '1,2,3,4,5'},
    {'id': 12, 'title': '食器の片づけ・キッチンリセット', 'type': 'daily', 'target': 'dad', 'exp': 80, 'gold': 50, 'icon': '🍽️'},
    {'id': 13, 'title': '排便日時記録 (健康管理)', 'type': 'daily', 'target': 'dad', 'exp': 10, 'gold': 10, 'icon': '📝'}, 
    {'id': 14, 'title': '体重計測 (健康管理)', 'type': 'daily', 'target': 'dad', 'exp': 10, 'gold': 10, 'icon': '⚖️'},
    {'id': 15, 'title': '洗濯物を干す', 'type': 'daily', 'target': 'dad', 'exp': 50, 'gold': 30, 'icon': '☀️'},
    {'id': 16, 'title': '洗濯物を畳む', 'type': 'daily', 'target': 'dad', 'exp': 40, 'gold': 30, 'icon': '👕'},
    {'id': 17, 'title': '洗濯物をしまう', 'type': 'daily', 'target': 'dad', 'exp': 30, 'gold': 20, 'icon': '🧺'},
    # 新規追加: 日曜朝のトイレ掃除
    {'id': 18, 'title': 'トイレ掃除 (念入り)', 'type': 'daily', 'target': 'dad', 'exp': 100, 'gold': 100, 'icon': '✨', 'days': '0', 'start_time': '06:00', 'end_time': '12:00'},

    # --- 母 (Mom) ---
    {'id': 20, 'title': '昼食を作る', 'type': 'daily', 'target': 'mom', 'exp': 100, 'gold': 100, 'icon': '🥪', 'start_time': '10:00', 'end_time': '14:00'},
    {'id': 21, 'title': '夕食を作る', 'type': 'daily', 'target': 'mom', 'exp': 150, 'gold': 150, 'icon': '🍳', 'start_time': '15:00', 'end_time': '20:00'},
    {'id': 22, 'title': '子供の寝かしつけ', 'type': 'daily', 'target': 'mom', 'exp': 300, 'gold': 200, 'icon': '🛌', 'start_time': '19:00', 'end_time': '23:59'},
    {'id': 23, 'title': '日中の家庭運営・育児基本給', 'type': 'daily', 'target': 'mom', 'exp': 250, 'gold': 50, 'icon': '🏠'},
    {'id': 24, 'title': '洗濯物を干す', 'type': 'daily', 'target': 'mom', 'exp': 50, 'gold': 30, 'icon': '☀️'},
    {'id': 25, 'title': '洗濯物を畳む', 'type': 'daily', 'target': 'mom', 'exp': 40, 'gold': 30, 'icon': '👕'},
    {'id': 26, 'title': '洗濯物をしまう', 'type': 'daily', 'target': 'mom', 'exp': 30, 'gold': 20, 'icon': '🧺'},

    # --- 期間限定イベント (Parents Only) ---
    {'id': 92, 'title': 'お雑煮を作る (年末限定)', 'type': 'limited', 'target': 'mom', 'exp': 80, 'gold': 80, 'icon': '🥪', 'start_date': '2024-12-31', 'end_date': '2026-1-1'},
]

# ==========================================
# 3. 報酬定義 (ショップメニュー)
# ==========================================
REWARDS = [
    # --- 既存: 食べ物・小休憩 ---
    {'id': 1, 'title': 'コンビニスイーツ購入権', 'category': 'food', 'cost_gold': 300, 'icon_key': '🍦'},
    {'id': 2, 'title': 'ビール/お酒アップグレード', 'category': 'food', 'cost_gold': 150, 'icon_key': '🍺'},
    {'id': 3, 'title': '休日・朝寝坊権利 (1時間)', 'category': 'service', 'cost_gold': 1000, 'icon_key': '🛌'},
    {'id': 4, 'title': '自由時間 (3時間)', 'category': 'service', 'cost_gold': 3000, 'icon_key': '🧘'},
    
    # --- 新規追加: エンタメ (Youtube) ---
    {'id': 10, 'title': 'Youtube (10分)', 'category': 'service', 'cost_gold': 50, 'icon_key': '📺'},
    {'id': 11, 'title': 'Youtube (30分)', 'category': 'service', 'cost_gold': 150, 'icon_key': '📺'},
    {'id': 12, 'title': 'Youtube (60分)', 'category': 'service', 'cost_gold': 300, 'icon_key': '📺'},

    # --- 新規追加: 物品・チケット ---
    {'id': 13, 'title': '湯の華廊 チケット', 'category': 'special', 'cost_gold': 1000, 'icon_key': '♨️'},
    {'id': 14, 'title': 'チョコレート (3000円分)', 'category': 'food', 'cost_gold': 3000, 'icon_key': '🍫'},
    {'id': 15, 'title': 'スマートウォッチ', 'category': 'item', 'cost_gold': 15000, 'icon_key': '⌚'},

    # --- スペシャル ---
    {'id': 99, 'title': 'ユニバのチケット (ペア)', 'category': 'special', 'cost_gold': 30000, 'icon_key': '🎢'},
    {'id': 100, 'title': 'ホテルに宿泊 (家族旅行)', 'category': 'special', 'cost_gold': 50000, 'icon_key': '🏨'},
]

# ==========================================
# 4. 装備品定義 (Equipment)
# ==========================================
EQUIPMENTS = [
    # --- 武器 (Weapon) ---
    {'id': 1, 'name': '木の棒', 'type': 'weapon', 'power': 3, 'cost': 50, 'icon': '🪵'},
    {'id': 2, 'name': '銅の剣', 'type': 'weapon', 'power': 10, 'cost': 200, 'icon': '🗡️'},
    {'id': 3, 'name': '鋼の剣', 'type': 'weapon', 'power': 25, 'cost': 800, 'icon': '⚔️'},
    {'id': 4, 'name': '勇者の剣', 'type': 'weapon', 'power': 50, 'cost': 5000, 'icon': '✨'},
    
    # --- 防具 (Armor) ---
    {'id': 101, 'name': '布の服', 'type': 'armor', 'power': 3, 'cost': 50, 'icon': '👕'},
    {'id': 102, 'name': '皮の鎧', 'type': 'armor', 'power': 10, 'cost': 200, 'icon': '🦺'},
    {'id': 103, 'name': '鉄の鎧', 'type': 'armor', 'power': 25, 'cost': 800, 'icon': '🛡️'},
    {'id': 104, 'name': '光の鎧', 'type': 'armor', 'power': 50, 'cost': 5000, 'icon': '🌟'},
]

# ==========================================
# 5. ボスモンスター定義 (Boss)
# ==========================================
BOSSES = [
    {'id': 1, 'name': 'ホコリ・スライム', 'hp': 200, 'exp': 100, 'gold': 100, 'icon': '🦠', 'desc': '部屋の隅から生まれた魔物。弱い。'},
    {'id': 2, 'name': 'ヌギッパ・ウルフ', 'hp': 600, 'exp': 300, 'gold': 300, 'icon': '🐺', 'desc': '服を脱ぎっぱなしにする獣。'},
    {'id': 3, 'name': 'ゾンビ・ディッシュ', 'hp': 1500, 'exp': 800, 'gold': 800, 'icon': '🧟', 'desc': '洗い場に溜まった皿の怨念。'},
    {'id': 4, 'name': '散らかりドラゴン', 'hp': 3000, 'exp': 2000, 'gold': 2000, 'icon': '🐉', 'desc': '全てを散乱させる巨竜。'},
    {'id': 5, 'name': '魔王カジ・ホウキ', 'hp': 10000, 'exp': 10000, 'gold': 10000, 'icon': '😈', 'desc': '家事の根源にしてラスボス。'}
]