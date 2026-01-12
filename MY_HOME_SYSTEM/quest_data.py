"""
Family Quest Master Data - Phase 3 Expansion (Educated & Gamified)
[2026-01-10 更新]
- 教育工学・行動経済学に基づきクエストと報酬を再設計
- ターゲット別クエスト（知育・徳育・体育・生活）の大幅拡充
- 経済バランス調整（インフレ抑制 vs スタートダッシュ）
- 装備品ラインナップを倍増（ドラクエ風）
"""

# ==========================================
# 1. ユーザー定義 (Users)
# ==========================================
USERS = [
    {
        'user_id': 'dad', 'name': 'まさひろ', 'job_class': '会社員', 
        'level': 1, 'exp': 0, 'gold': 0, 'avatar': '⚔️',
        'info': '35歳 / INTJ / 三菱電機勤務 / 186cm / 住宅ローン5,400万の守護者' 
    },
    {
        'user_id': 'mom', 'name': 'はるな', 'job_class': '専業主婦', 
        'level': 1, 'exp': 0, 'gold': 0, 'avatar': '🪄',
        'info': '32歳 / 育児・家庭運営責任者 / 美容と健康の求道者' 
    },
    {
        'user_id': 'son', 'name': 'ともや', 'job_class': '年長', 
        'level': 1, 'exp': 0, 'gold': 0, 'avatar': '👦',
        'info': '5歳 / 学習習慣形成・ヒーロー見習い' 
    },
    {
        'user_id': 'daughter', 'name': 'すずか', 'job_class': '遊び人', 
        'level': 1, 'exp': 0, 'gold': 0, 'avatar': '👶',
        'info': '2歳 / イヤイヤ期の妖精' 
    }
]

# ==========================================
# 2. クエスト定義 (Quests)
# ==========================================
# category: life(生活), study(学習), house(家事), work(仕事), health(健康), moral(徳育), sport(体育)
# difficulty: E(簡単/5-10G), D(普通/10-30G), C(努力/30-80G), B(困難/100-300G), A(激務/300-800G), S(伝説/1000G~)

QUESTS = [
    # --- 共通: 基本生活習慣 (朝) ---
    {'id': 1, 'title': 'お着替え (準備含む)', 'type': 'daily', 'target': 'all', 'category': 'life', 'difficulty': 'D', 'exp': 20, 'gold': 10, 'icon': '👕', 'start_time': '05:00', 'end_time': '08:00'},
    {'id': 2, 'title': 'はみがき (朝)', 'type': 'daily', 'target': 'all', 'category': 'life', 'difficulty': 'E', 'exp': 15, 'gold': 5, 'icon': '🪥', 'start_time': '05:00', 'end_time': '09:00'},
    {'id': 901, 'title': 'お皿洗い', 'type': 'infinite', 'target': 'all', 'category': 'house', 'difficulty': 'C', 'exp': 15, 'gold': 50, 'icon': '🍽️', 'desc': 'ご飯のあとのお皿をきれいに洗おう', 'chance': 1.0},
    
    # --- 共通: 協力・お手伝い ---
    {'id': 7, 'title': 'ルンバの水交換', 'type': 'daily', 'target': 'all', 'category': 'house', 'difficulty': 'C', 'exp': 50, 'gold': 30, 'icon': '🤖', 'days': '6'},
    {'id': 8, 'title': '寝る前のおもちゃ片付け', 'type': 'daily', 'target': 'all', 'category': 'life', 'difficulty': 'C', 'exp': 40, 'gold': 20, 'icon': '🧸', 'start_time': '19:00', 'end_time': '21:00'},

    # --- 智矢 (Son) : 知育・徳育・体育のバランス ---
    # 生活
    {'id': 40, 'title': '朝のトイレに行く', 'type': 'daily', 'target': 'son', 'category': 'life', 'difficulty': 'E', 'exp': 10, 'gold': 5, 'icon': '🚽'},
    {'id': 41, 'title': '寝る前のトイレに行く', 'type': 'daily', 'target': 'son', 'category': 'life', 'difficulty': 'E', 'exp': 10, 'gold': 5, 'icon': '🚽'},
    {'id': 42, 'title': '朝起きたら顔を洗う', 'type': 'daily', 'target': 'son', 'category': 'life', 'difficulty': 'E', 'exp': 10, 'gold': 5, 'icon': '🧖'},
    {'id': 3, 'title': '朝ごはんを食べる (完食)', 'type': 'daily', 'target': 'son', 'category': 'health', 'difficulty': 'D', 'exp': 20, 'gold': 10, 'icon': '🍳'},
    {'id': 101, 'title': '幼稚園に行く', 'type': 'daily', 'target': 'son', 'category': 'study', 'difficulty': 'A', 'exp': 100, 'gold': 100, 'icon': '🏢'},
    # 知育 (Study)
    {'id': 43, 'title': '一人で本を読む', 'type': 'daily', 'target': 'son', 'category': 'study', 'difficulty': 'C', 'exp': 30, 'gold': 15, 'icon': '📖'},
    {'id': 30, 'title': '国語プリント完了', 'type': 'daily', 'target': 'son', 'category': 'study', 'difficulty': 'C', 'exp': 50, 'gold': 30, 'icon': '📝'},
    {'id': 31, 'title': '算数プリント完了', 'type': 'daily', 'target': 'son', 'category': 'study', 'difficulty': 'C', 'exp': 50, 'gold': 30, 'icon': '🧮'},
    {'id': 45, 'title': 'ピアノの練習', 'type': 'daily', 'target': 'son', 'category': 'study', 'difficulty': 'C', 'exp': 50, 'gold': 30, 'icon': '🎹'},
    # [新規] 知育拡張
    {'id': 50, 'title': '時計を見て時間を教える', 'type': 'daily', 'target': 'son', 'category': 'study', 'difficulty': 'D', 'exp': 20, 'gold': 10, 'icon': '🕰️'},
    {'id': 51, 'title': '明日の天気予報を確認する', 'type': 'daily', 'target': 'son', 'category': 'study', 'difficulty': 'E', 'exp': 15, 'gold': 5, 'icon': '☀️'},
    # [新規] 徳育 (Moral)
    {'id': 44, 'title': '靴を並べるお手伝い', 'type': 'daily', 'target': 'son', 'category': 'moral', 'difficulty': 'E', 'exp': 20, 'gold': 10, 'icon': '👞'},
    {'id': 52, 'title': '妹におもちゃを貸してあげる', 'type': 'infinite', 'target': 'son', 'category': 'moral', 'difficulty': 'D', 'exp': 30, 'gold': 10, 'icon': '🤝'},
    {'id': 53, 'title': '「ありがとう」を言う', 'type': 'infinite', 'target': 'son', 'category': 'moral', 'difficulty': 'E', 'exp': 10, 'gold': 5, 'icon': '🗣️'},
    # [新規] 体育 (Sport)
    {'id': 54, 'title': '縄跳び 10回成功', 'type': 'daily', 'target': 'son', 'category': 'sport', 'difficulty': 'C', 'exp': 30, 'gold': 20, 'icon': '🏃'},
    {'id': 55, 'title': '公園で全力で遊ぶ (30分)', 'type': 'daily', 'target': 'son', 'category': 'sport', 'difficulty': 'C', 'exp': 50, 'gold': 20, 'icon': '⛲'},
    
    # [新規] お手伝い・週末 (Weekend)
    {'id': 48, 'title': 'ママのお手伝い', 'type': 'infinite', 'target': 'son', 'category': 'house', 'difficulty': 'D', 'exp': 30, 'gold': 15, 'icon': '🧚', 'desc': 'ママに頼まれたことをやろう'},
    {'id': 46, 'title': '休みの日は買い物についてくる', 'type': 'daily', 'target': 'son', 'category': 'house', 'difficulty': 'B', 'exp': 100, 'gold': 50, 'icon': '🛒', 'days': '0,6'},
    {'id': 56, 'title': '自分の部屋の掃除・片付け', 'type': 'daily', 'target': 'son', 'category': 'house', 'difficulty': 'B', 'exp': 150, 'gold': 100, 'icon': '🧹', 'days': '0,6', 'desc': '週末は自分の城をきれいにしよう'},
    
    # ボーナス
    {'id': 47, 'title': '朝起きておねしょをしていない', 'type': 'daily', 'target': 'son', 'category': 'life', 'difficulty': 'A', 'exp': 100, 'gold': 50, 'icon': '✨'},

    # --- 涼花 (Daughter) : 基本的生活習慣の定着 ---
    {'id': 301, 'title': '朝ごはんを食べる (完食)', 'type': 'daily', 'target': 'daughter', 'category': 'health', 'difficulty': 'D', 'exp': 20, 'gold': 10, 'icon': '🍳'},
    # [新規] 生活 (Life)
    {'id': 302, 'title': 'トイレでおしっこ成功', 'type': 'infinite', 'target': 'daughter', 'category': 'life', 'difficulty': 'B', 'exp': 50, 'gold': 30, 'icon': '🚽', 'desc': 'トイトレ頑張ろう！'},
    {'id': 303, 'title': '野菜を一口食べる', 'type': 'daily', 'target': 'daughter', 'category': 'health', 'difficulty': 'A', 'exp': 50, 'gold': 50, 'icon': '🥦', 'desc': '嫌いなものでも一口！'},
    {'id': 304, 'title': 'パジャマを自分で着る', 'type': 'daily', 'target': 'daughter', 'category': 'life', 'difficulty': 'C', 'exp': 30, 'gold': 20, 'icon': '👚'},
    {'id': 305, 'title': '外から帰ったら手洗い・うがい', 'type': 'daily', 'target': 'daughter', 'category': 'health', 'difficulty': 'D', 'exp': 20, 'gold': 10, 'icon': '🧼'},
    {'id': 306, 'title': 'お出かけの時に靴を履く', 'type': 'daily', 'target': 'daughter', 'category': 'life', 'difficulty': 'E', 'exp': 15, 'gold': 5, 'icon': '👟'},

    # --- 共通: 基本生活習慣 (夜) ---
    {'id': 4, 'title': 'はみがき (夜)', 'type': 'daily', 'target': 'all', 'category': 'life', 'difficulty': 'E', 'exp': 15, 'gold': 15, 'icon': '🪥', 'start_time': '17:00', 'end_time': '20:00'},
    {'id': 5, 'title': 'お風呂にはいる', 'type': 'daily', 'target': 'all', 'category': 'life', 'difficulty': 'D', 'exp': 20, 'gold': 10, 'icon': '🛁', 'start_time': '17:00', 'end_time': '20:00'},
    {'id': 6, 'title': '明日の準備', 'type': 'daily', 'target': 'son', 'category': 'life', 'difficulty': 'D', 'exp': 30, 'gold': 30, 'icon': '🎒', 'start_time': '17:00', 'end_time': '20:00'},

    # --- 父 (Dad) : 仕事・家事・健康のハイブリッド ---
    {'id': 10, 'title': '会社勤務 (通常)', 'type': 'daily', 'target': 'dad', 'category': 'work', 'difficulty': 'C', 'exp': 200, 'gold': 100, 'icon': '🏢', 'days': '1,2,3,4,5'},
    {'id': 11, 'title': '会社勤務 (高負荷・残業)', 'type': 'daily', 'target': 'dad', 'category': 'work', 'difficulty': 'A', 'exp': 350, 'gold': 200, 'icon': '🔥', 'days': '1,2,3,4,5'},
    {'id': 12, 'title': '食器の片づけ・キッチンリセット', 'type': 'daily', 'target': 'dad', 'category': 'house', 'difficulty': 'C', 'exp': 80, 'gold': 50, 'icon': '🍽️'},
    {'id': 13, 'title': '排便日時記録 (健康管理)', 'type': 'daily', 'target': 'dad', 'category': 'health', 'difficulty': 'E', 'exp': 10, 'gold': 10, 'icon': '📝'}, 
    {'id': 14, 'title': '体重計測 (健康管理)', 'type': 'daily', 'target': 'dad', 'category': 'health', 'difficulty': 'E', 'exp': 10, 'gold': 10, 'icon': '⚖️'},
    {'id': 15, 'title': '洗濯物を干す', 'type': 'daily', 'target': 'dad', 'category': 'house', 'difficulty': 'C', 'exp': 50, 'gold': 30, 'icon': '☀️'},
    {'id': 16, 'title': '洗濯物を畳む', 'type': 'daily', 'target': 'dad', 'category': 'house', 'difficulty': 'C', 'exp': 40, 'gold': 30, 'icon': '👕'},
    {'id': 17, 'title': '洗濯物をしまう', 'type': 'daily', 'target': 'dad', 'category': 'house', 'difficulty': 'D', 'exp': 30, 'gold': 20, 'icon': '🧺'},
    {'id': 18, 'title': 'トイレ掃除 (念入り)', 'type': 'daily', 'target': 'dad', 'category': 'house', 'difficulty': 'B', 'exp': 100, 'gold': 100, 'icon': '✨', 'days': '0'},
    # [新規] 家事 (House)
    {'id': 60, 'title': 'お風呂掃除', 'type': 'daily', 'target': 'dad', 'category': 'house', 'difficulty': 'C', 'exp': 50, 'gold': 40, 'icon': '🧽'},
    {'id': 61, 'title': '週末の夕食を作る', 'type': 'daily', 'target': 'dad', 'category': 'house', 'difficulty': 'A', 'exp': 300, 'gold': 200, 'icon': '👨‍🍳', 'days': '0,6', 'desc': 'ママを休ませるための男飯'},
    # [新規] 健康 (Health)
    {'id': 62, 'title': 'ランニング 5km', 'type': 'daily', 'target': 'dad', 'category': 'health', 'difficulty': 'A', 'exp': 200, 'gold': 50, 'icon': '🏃‍♂️', 'desc': '体力向上・ダイエット'},
    {'id': 63, 'title': '筋トレ 20分', 'type': 'daily', 'target': 'dad', 'category': 'health', 'difficulty': 'B', 'exp': 100, 'gold': 30, 'icon': '💪'},
    # [新規] 育児 (Family)
    {'id': 64, 'title': '子供の寝かしつけ担当', 'type': 'daily', 'target': 'dad', 'category': 'life', 'difficulty': 'B', 'exp': 150, 'gold': 0, 'icon': '🛌', 'desc': 'ママに自由時間を'},

    # --- 母 (Mom) ---
    {'id': 20, 'title': '昼食を作る', 'type': 'daily', 'target': 'mom', 'category': 'house', 'difficulty': 'B', 'exp': 100, 'gold': 100, 'icon': '🥪'},
    {'id': 21, 'title': '夕食を作る', 'type': 'daily', 'target': 'mom', 'category': 'house', 'difficulty': 'A', 'exp': 150, 'gold': 150, 'icon': '🍳'},
    {'id': 22, 'title': '子供の寝かしつけ', 'type': 'daily', 'target': 'mom', 'category': 'life', 'difficulty': 'A', 'exp': 300, 'gold': 200, 'icon': '🛌'},
    {'id': 23, 'title': '日中の家庭運営・育児基本給', 'type': 'daily', 'target': 'mom', 'category': 'work', 'difficulty': 'S', 'exp': 250, 'gold': 50, 'icon': '🏠'},
    {'id': 24, 'title': '洗濯物を干す', 'type': 'daily', 'target': 'mom', 'category': 'house', 'difficulty': 'C', 'exp': 50, 'gold': 30, 'icon': '☀️'},
    {'id': 25, 'title': '洗濯物を畳む', 'type': 'daily', 'target': 'mom', 'category': 'house', 'difficulty': 'C', 'exp': 40, 'gold': 30, 'icon': '👕'},
    {'id': 26, 'title': '洗濯物をしまう', 'type': 'daily', 'target': 'mom', 'category': 'house', 'difficulty': 'D', 'exp': 30, 'gold': 20, 'icon': '🧺'},

    # --- レア・ボス級 (Rare) ---
    {'id': 991, 'title': '大掃除 (家族全員)', 'type': 'limited', 'target': 'all', 'category': 'house', 'difficulty': 'S', 'exp': 1000, 'gold': 500, 'icon': '🧹', 'desc': '年末等の大イベント'},
    {'id': 992, 'title': '予防接種を受ける', 'type': 'limited', 'target': 'son', 'category': 'health', 'difficulty': 'S', 'exp': 500, 'gold': 300, 'icon': '💉', 'desc': '泣かずに頑張る'},
    {'id': 92, 'title': 'お雑煮を作る (年末限定)', 'type': 'limited', 'target': 'mom', 'category': 'house', 'difficulty': 'A', 'exp': 80, 'gold': 80, 'icon': '🥪', 'start_date': '2024-12-31', 'end_date': '2026-01-01'},
]

# ==========================================
# 3. 報酬定義 (Rewards)
# ==========================================
REWARDS = [
    # --- Small (消費型: 100G〜500G) ---
    {'id': 1, 'title': 'コンビニスイーツ購入権', 'category': 'food', 'cost_gold': 300, 'icon_key': '🍦'},
    {'id': 2, 'title': 'ビール/お酒アップグレード', 'category': 'food', 'cost_gold': 150, 'icon_key': '🍺'},
    {'id': 10, 'title': 'Youtube (10分)', 'category': 'service', 'cost_gold': 50, 'icon_key': '📺'},
    {'id': 11, 'title': 'Youtube (30分)', 'category': 'service', 'cost_gold': 150, 'icon_key': '📺'},
    {'id': 12, 'title': 'Youtube (60分)', 'category': 'service', 'cost_gold': 300, 'icon_key': '📺'},
    {'id': 20, 'title': 'ガチャガチャ 1回', 'category': 'item', 'cost_gold': 400, 'icon_key': '💊'},
    {'id': 21, 'title': '好きなおやつ 1個', 'category': 'food', 'cost_gold': 100, 'icon_key': '🍪'},

    # --- Medium (体験型: 500G〜3000G) ---
    {'id': 3, 'title': '休日・朝寝坊権利 (1時間)', 'category': 'service', 'cost_gold': 1000, 'icon_key': '🛌'},
    {'id': 4, 'title': '自由時間 (3時間)', 'category': 'service', 'cost_gold': 3000, 'icon_key': '🧘'},
    {'id': 13, 'title': '湯の華廊 チケット', 'category': 'special', 'cost_gold': 1000, 'icon_key': '♨️'},
    {'id': 14, 'title': 'チョコレート (3000円分)', 'category': 'food', 'cost_gold': 3000, 'icon_key': '🍫'},
    {'id': 22, 'title': '夜更かしチケット (30分)', 'category': 'service', 'cost_gold': 500, 'icon_key': '🌙'},
    {'id': 23, 'title': '夕飯リクエスト権', 'category': 'service', 'cost_gold': 800, 'icon_key': '🍽️'},

    # --- Large (目標型: 5000G〜20000G) ---
    {'id': 15, 'title': 'スマートウォッチ', 'category': 'item', 'cost_gold': 15000, 'icon_key': '⌚'},
    {'id': 24, 'title': '好きなおもちゃ (小)', 'category': 'item', 'cost_gold': 5000, 'icon_key': '🤖'},
    {'id': 25, 'title': '回転寿司に行く権', 'category': 'special', 'cost_gold': 8000, 'icon_key': '🍣'},
    {'id': 26, 'title': '映画館に行く権 (ポップコーン付)', 'category': 'special', 'cost_gold': 6000, 'icon_key': '🎬'},

    # --- Premium (夢の報酬: 30000G〜) ---
    {'id': 99, 'title': 'ユニバのチケット (ペア)', 'category': 'special', 'cost_gold': 30000, 'icon_key': '🎢'},
    {'id': 100, 'title': 'ホテルに宿泊 (家族旅行)', 'category': 'special', 'cost_gold': 50000, 'icon_key': '🏨'},
    {'id': 101, 'title': 'Switchのゲームソフト 1本', 'category': 'item', 'cost_gold': 40000, 'icon_key': '🎮'},
    {'id': 102, 'title': 'SHARP ヘルシオ ホットクック', 'category': 'item', 'cost_gold': 60000, 'icon_key': '🍲', 'desc': '家事の時間を減らして家族の時間を増やす魔法の鍋'},
]

# ==========================================
# 4. 装備品定義 (Equipment)
# ==========================================
# 初心者用から伝説の装備まで、ドラクエ風に拡充
EQUIPMENTS = [
    # --- 武器 (Weapon) ---
    {'id': 1, 'name': 'ひのきのぼう', 'type': 'weapon', 'power': 2, 'cost': 30, 'icon': '🪵', 'desc': '旅立ちの第一歩。安い。'},
    {'id': 2, 'name': '銅の剣', 'type': 'weapon', 'power': 10, 'cost': 200, 'icon': '🗡️', 'desc': '少し強くなった気がする剣。'},
    {'id': 5, 'name': '鉄の槍', 'type': 'weapon', 'power': 18, 'cost': 450, 'icon': '🔱', 'desc': 'リーチが長い。'},
    {'id': 3, 'name': '鋼の剣', 'type': 'weapon', 'power': 25, 'cost': 800, 'icon': '⚔️', 'desc': '一人前の証。'},
    {'id': 6, 'name': 'はじゃのつるぎ', 'type': 'weapon', 'power': 35, 'cost': 1500, 'icon': '🎇', 'desc': '光り輝く刀身。'},
    {'id': 7, 'name': 'ドラゴンキラー', 'type': 'weapon', 'power': 45, 'cost': 3000, 'icon': '🐉', 'desc': 'ドラゴン特攻がある気がする。'},
    {'id': 4, 'name': '勇者の剣', 'type': 'weapon', 'power': 60, 'cost': 5000, 'icon': '✨', 'desc': '伝説の勇者が使っていた剣。'},
    {'id': 8, 'name': 'メタルキングの剣', 'type': 'weapon', 'power': 100, 'cost': 15000, 'icon': '👑', 'desc': '最強の破壊力。'},

    # --- 防具 (Armor) ---
    {'id': 101, 'name': '布の服', 'type': 'armor', 'power': 3, 'cost': 50, 'icon': '👕', 'desc': 'ただの服。'},
    {'id': 105, 'name': '旅人の服', 'type': 'armor', 'power': 6, 'cost': 100, 'icon': '🧥', 'desc': '動きやすい服。'},
    {'id': 102, 'name': '皮の鎧', 'type': 'armor', 'power': 10, 'cost': 200, 'icon': '🦺', 'desc': '軽くて丈夫。'},
    {'id': 106, 'name': 'みかわしの服', 'type': 'armor', 'power': 15, 'cost': 600, 'icon': '💃', 'desc': '攻撃をよけやすくなる。'},
    {'id': 103, 'name': '鉄の鎧', 'type': 'armor', 'power': 25, 'cost': 800, 'icon': '🛡️', 'desc': '重いが防御力は高い。'},
    {'id': 107, 'name': '魔法の鎧', 'type': 'armor', 'power': 35, 'cost': 2000, 'icon': '🔮', 'desc': '魔法耐性がつくかもしれない。'},
    {'id': 104, 'name': '光の鎧', 'type': 'armor', 'power': 50, 'cost': 5000, 'icon': '🌟', 'desc': '歩くたびにHPが回復する気分になれる。'},
    {'id': 108, 'name': 'メタルキングの鎧', 'type': 'armor', 'power': 90, 'cost': 12000, 'icon': '💎', 'desc': '全てを跳ね返す最強の鎧。'},
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