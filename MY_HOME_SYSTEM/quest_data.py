"""
Family Quest Master Data - Phase 4.1 (Complete Descriptions)
[2026-01-14 更新]
- クエストに加え、報酬(REWARDS)にも説明文(desc)を完全実装
- ショップでの購買意欲を高め、経済サイクルを活性化させる
- UI/UXの統一感を向上
"""
"""
Family Quest Master Data - Phase 5.1 (Boss Expansion & Price Adjustment)
[2026-01-24 更新]
- ボスモンスターの種類を拡充し、倒す（克服する）楽しみを追加
- 報酬リストの価格適正化（回転寿司の値下げ、子供向けチケットの追加）
- パパのアルコール報酬削除
"""

# ==========================================
# 0. 定数・設定 (Constants)
# ==========================================
# Days Key: 0=月, 1=火, 2=水, 3=木, 4=金, 5=土, 6=日

# ==========================================
# 1. ユーザー定義 (Users)
# ==========================================
USERS = [
    {
        'user_id': 'dad', 'name': 'まさひろ', 'job_class': '会社員', 
        'level': 1, 'exp': 0, 'gold': 0, 'avatar': '⚔️',
        'info': '35歳 / INTJ / 通勤電車を書斎に変える男 / 住宅ローン5,400万の守護者' 
    },
    {
        'user_id': 'mom', 'name': 'はるな', 'job_class': '専業主婦', 
        'level': 1, 'exp': 0, 'gold': 0, 'avatar': '🪄',
        'info': '32歳 / 育児・家庭運営責任者 / 伝説の秘宝「アルハンブラ」を目指す者' 
    },
    {
        'user_id': 'son', 'name': 'ともや', 'job_class': 'もうすぐ1年生', 
        'level': 1, 'exp': 0, 'gold': 0, 'avatar': '👦',
        'info': '5歳 / 文武両道・早起きのヒーロー / YouTubeより稼げる仕事を探求中' 
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
    # ==========================================
    # 【A】 通常クエスト (Daily Quests)
    # ==========================================
    
    # ------------------------------------------
    # A-1. 通常：共通 (All)
    # ------------------------------------------
    {'id': 5, 'title': 'お風呂にはいる', 'type': 'daily', 'target': 'all', 'category': 'life', 'difficulty': 'D', 'exp': 20, 'gold': 10, 'icon': '🛁', 'start_time': '17:00', 'end_time': '21:00', 'desc': '一日の汚れを落としてさっぱりしよう'},
    {'id': 1100, 'title': '【朝】毎朝ミッション', 'type': 'daily', 'target': 'all', 'category': 'life', 'difficulty': 'C', 'exp': 50, 'gold': 80, 'icon': '🌅', 'start_time': '06:30', 'end_time': '08:30', 'desc': 'トイレ・洗顔・着替え・朝ごはん完食。全部できたらクリア！'},
    {'id': 1105, 'title': '【夜】就寝ミッション', 'type': 'daily', 'target': 'all', 'category': 'life', 'difficulty': 'C', 'exp': 50, 'gold': 70, 'icon': '🌙', 'start_time': '19:00', 'end_time': '21:00', 'desc': 'トイレ・歯磨き・お片付け完了。パパママにおやすみなさい！'},

    # ------------------------------------------
    # A-2. 通常：パパ (Dad)
    # ------------------------------------------
    {'id': 10, 'title': '会社勤務 (通常)', 'type': 'daily', 'target': 'dad', 'category': 'work', 'difficulty': 'C', 'exp': 200, 'gold': 100, 'icon': '🏢', 'days': '0,1,2,3,4', 'desc': '家族の生活基盤を守るための戦い'},
    {'id': 13, 'title': '排便日時記録 (健康管理)', 'type': 'daily', 'target': 'dad', 'category': 'health', 'difficulty': 'E', 'exp': 10, 'gold': 10, 'icon': '📝', 'desc': '腸内環境のモニタリング'}, 
    {'id': 12, 'title': '食器の片づけ・キッチンリセット', 'type': 'daily', 'target': 'dad', 'category': 'house', 'difficulty': 'C', 'exp': 80, 'gold': 50, 'icon': '🍽️', 'desc': 'シンクをピカピカにして明日を迎える'},

 
    # ------------------------------------------
    # A-3. 通常：ママ (Mom)
    # ------------------------------------------
    {'id': 20, 'title': '昼食を作る', 'type': 'daily', 'target': 'mom', 'category': 'house', 'difficulty': 'B', 'exp': 100, 'gold': 100, 'icon': '🥪', 'start_time': '11:00', 'end_time': '14:00', 'desc': '休日のエネルギー補給'},
    {'id': 21, 'title': '夕食を作る', 'type': 'daily', 'target': 'mom', 'category': 'house', 'difficulty': 'A', 'exp': 150, 'gold': 150, 'icon': '🍳', 'start_time': '16:00', 'end_time': '20:00', 'desc': '家族の健康を作る毎日の錬金術'},
    {'id': 23, 'title': '日中の家庭運営・育児基本給', 'type': 'daily', 'target': 'mom', 'category': 'work', 'difficulty': 'S', 'exp': 250, 'gold': 50, 'icon': '🏠', 'desc': '見えない家事と育児への報酬'},
    {'id': 1000, 'title': 'ゴミ捨て (燃えるゴミ)', 'type': 'daily', 'target': 'mom', 'category': 'house', 'difficulty': 'D', 'exp': 30, 'gold': 15, 'icon': '🔥', 'days': '0,3', 'desc': '月・木は必ず遂行せよ', 'start_time': '08:00', 'end_time': '12:00'},
    {'id': 1001, 'title': 'ゴミ捨て (プラスチック)', 'type': 'daily', 'target': 'mom', 'category': 'house', 'difficulty': 'D', 'exp': 30, 'gold': 15, 'icon': '♻️', 'days': '2', 'desc': '水曜日のプラゴミ回収', 'start_time': '08:00', 'end_time': '12:00'},
    {'id': 1002, 'title': 'ゴミ捨て (ペットボトル)', 'type': 'daily', 'target': 'mom', 'category': 'house', 'difficulty': 'D', 'exp': 30, 'gold': 15, 'icon': '🧴', 'days': '4', 'desc': '金曜日の資源回収', 'start_time': '08:00', 'end_time': '12:00'},
    {'id': 1006, 'title': '幼稚園の連絡帳記入', 'type': 'daily', 'target': 'mom', 'category': 'house', 'difficulty': 'E', 'exp': 20, 'gold': 10, 'icon': '✍️', 'days': '0,1,2,3,4', 'desc': '毎日の体調と様子を報告'},
    {'id': 1007, 'title': 'みらいの連絡帳記入', 'type': 'daily', 'target': 'mom', 'category': 'house', 'difficulty': 'E', 'exp': 20, 'gold': 10, 'icon': '📒', 'days': '6', 'desc': '日曜日は療育の記録'},
    {'id': 1008, 'title': '休日の朝の会 開催', 'type': 'daily', 'target': 'mom', 'category': 'life', 'difficulty': 'C', 'exp': 50, 'gold': 30, 'icon': '🌅', 'days': '5,6', 'desc': '休日のスケジュール確認と挨拶', 'start_time': '07:00', 'end_time': '10:00'},


    # ------------------------------------------
    # A-4. 通常：智矢 (Son)
    # ------------------------------------------
    {'id': 1101, 'title': '登校タイムアタック (07:50)', 'type': 'daily', 'target': 'son', 'category': 'life', 'difficulty': 'B', 'exp': 100, 'gold': 50, 'icon': '⏱️', 'start_time': '07:00', 'end_time': '07:50', 'desc': '7:50までに靴を履いて玄関に立てたら成功！'},
    {'id': 101, 'title': '幼稚園に行く', 'type': 'daily', 'target': 'son', 'category': 'study', 'difficulty': 'A', 'exp': 100, 'gold': 100, 'icon': '🏢', 'days': '0,1,2,3,4', 'desc': '今日も元気に登園しよう'},
    {'id': 44, 'title': '靴を並べる', 'type': 'daily', 'target': 'son', 'category': 'moral', 'difficulty': 'E', 'exp': 20, 'gold': 10, 'icon': '👞', 'desc': '玄関をきれいに'},
    {'id': 1020, 'title': '基地のセキュリティチェック', 'type': 'daily', 'target': 'son', 'category': 'house', 'difficulty': 'D', 'exp': 30, 'gold': 15, 'icon': '🔒', 'desc': '寝る前に戸締まりを確認して報告せよ', 'start_time': '19:00', 'end_time': '20:30'},
    {'id': 1021, 'title': '明日の装備確認', 'type': 'daily', 'target': 'son', 'category': 'study', 'difficulty': 'C', 'exp': 40, 'gold': 20, 'icon': '🎒', 'desc': 'カバンの中身を全部出して再点検'},

    # ------------------------------------------
    # A-5. 通常：涼花 (Daughter)
    # ------------------------------------------
    {'id': 301, 'title': '朝ごはんを食べる (完食)', 'type': 'daily', 'target': 'daughter', 'category': 'health', 'difficulty': 'D', 'exp': 20, 'gold': 10, 'icon': '🍳', 'start_time': '07:00', 'end_time': '08:30', 'desc': 'もぐもぐ食べて大きくなろう'},
    {'id': 303, 'title': '野菜を一口食べる', 'type': 'daily', 'target': 'daughter', 'category': 'health', 'difficulty': 'A', 'exp': 50, 'gold': 50, 'icon': '🥦', 'desc': '嫌いなものでも一口！'},
    {'id': 304, 'title': 'パジャマを自分で着る', 'type': 'daily', 'target': 'daughter', 'category': 'life', 'difficulty': 'C', 'exp': 30, 'gold': 20, 'icon': '👚', 'start_time': '19:00', 'end_time': '20:30', 'desc': 'ボタンも自分で留められるかな？'},


    # ==========================================
    # 【B】 特別クエスト (Special / Infinite / Limited)
    # ==========================================
    
    # ------------------------------------------
    # B-1. 特別：共通 (All)
    # ------------------------------------------
    {'id': 7, 'title': 'ルンバの水交換', 'type': 'special', 'target': 'all', 'category': 'house', 'difficulty': 'C', 'exp': 50, 'gold': 30, 'icon': '🤖', 'days': '6', 'desc': '掃除ロボットのメンテナンス任務'},
    {'id': 901, 'title': 'お皿洗い', 'type': 'infinite', 'target': 'all', 'category': 'house', 'difficulty': 'C', 'exp': 15, 'gold': 50, 'icon': '🍽️', 'desc': 'ご飯のあとのお皿をきれいに洗おう', 'chance': 1.0},

    # ------------------------------------------
    # B-2. 特別：パパ (Dad)
    # ------------------------------------------
    {'id': 11, 'title': '会社勤務 (高負荷・残業)', 'type': 'special', 'target': 'dad', 'category': 'work', 'difficulty': 'A', 'exp': 350, 'gold': 200, 'icon': '🔥', 'days': '0,1,2,3,4', 'desc': '激務を乗り越え、多額の報酬を得る'},
    {'id': 18, 'title': 'トイレ掃除 (念入り)', 'type': 'special', 'target': 'dad', 'category': 'house', 'difficulty': 'B', 'exp': 100, 'gold': 100, 'icon': '✨', 'days': '6', 'desc': 'トイレの神様にご挨拶。金運UP?'},
    {'id': 61, 'title': '週末の夕食を作る', 'type': 'special', 'target': 'dad', 'category': 'house', 'difficulty': 'A', 'exp': 300, 'gold': 200, 'icon': '👨‍🍳', 'days': '5,6', 'desc': 'ママを休ませるための男飯', 'start_time': '15:00', 'end_time': '20:00'},
    {'id': 65, 'title': '洗車', 'type': 'special', 'target': 'dad', 'category': 'house', 'difficulty': 'A', 'exp': 300, 'gold': 200, 'icon': '🚗', 'days': '5,6', 'desc': '愛車をピカピカに磨き上げる（※月1回までのセルフ運用）'},
    {'id': 9001, 'title': '【伊勢志摩】旅行計画会議', 'type': 'limited', 'target': 'dad', 'category': 'life', 'difficulty': 'D', 'exp': 50, 'gold': 50, 'icon': '🗺️', 'end_date': '2026-03-14', 'desc': '旅行のしおりや行き先を家族で話す'},
    {'id': 502, 'title': '寝室の布団上げ＆掃除', 'type': 'special', 'target': 'dad', 'days': '5,6', 'exp': 40, 'gold': 100, 'icon': '🛏️', 'desc': '布団をあげて掃除機をかける'},
    {'id': 501, 'title': '昨夜の寝かしつけ', 'type': 'special', 'target': 'dad', 'days': '0,1,2,3,4,5,6', 'exp': 300, 'gold': 200, 'icon': '💤', 'desc': '子供を寝かしつけた（翌朝申請用）'},
    {'id': 14, 'title': '体重計測 (健康管理)', 'type': 'special', 'target': 'dad', 'category': 'health', 'difficulty': 'E', 'exp': 10, 'gold': 10, 'icon': '⚖️', 'desc': '身体ステータスのチェック'},
    {'id': 15, 'title': '洗濯物を干す', 'type': 'special', 'target': 'dad', 'category': 'house', 'difficulty': 'C', 'exp': 50, 'gold': 30, 'icon': '☀️', 'desc': '日光の力で装備を浄化する'},
    {'id': 16, 'title': '洗濯物を畳む', 'type': 'special', 'target': 'dad', 'category': 'house', 'difficulty': 'C', 'exp': 40, 'gold': 30, 'icon': '👕', 'desc': '装備品を整理整頓する'},
    {'id': 17, 'title': '洗濯物をしまう', 'type': 'special', 'target': 'dad', 'category': 'house', 'difficulty': 'D', 'exp': 30, 'gold': 20, 'icon': '🧺', 'desc': 'それぞれのクローゼットへ格納'},
    {'id': 60, 'title': 'お風呂掃除', 'type': 'special', 'target': 'dad', 'category': 'house', 'difficulty': 'C', 'exp': 50, 'gold': 40, 'icon': '🧽', 'desc': '浴槽を磨いて湯船を準備する'},
    {'id': 1200, 'title': '通勤ハック：読書/資格勉強', 'type': 'special', 'target': 'dad', 'category': 'study', 'difficulty': 'C', 'exp': 150, 'gold': 50, 'icon': '🚃', 'days': '0,1,2,3,4', 'desc': '往復の電車内でスマホを見ずに自己研鑽 (生成AIパスポート等)'},

    # ------------------------------------------
    # B-3. 特別：ママ (Mom)
    # ------------------------------------------
    {'id': 503, 'title': '寝室の布団上げ＆掃除', 'type': 'special', 'target': 'mom', 'days': '5,6', 'exp': 40, 'gold': 100, 'icon': '🛏️', 'desc': '布団をあげて掃除機をかける'},
    {'id': 504, 'title': 'アクセ装着と片付け', 'type': 'special', 'target': 'mom', 'days': '5,6', 'exp': 15, 'gold': 20, 'icon': '💍', 'desc': '週末のおしゃれを楽しみ、定位置に戻す'},
    {'id': 1011, 'title': '女神のメンテナンス', 'type': 'special', 'target': 'mom', 'category': 'health', 'difficulty': 'D', 'exp': 40, 'gold': 20, 'icon': '🧖‍♀️', 'desc': 'パックやスキンケアで美を高める'},
    {'id': 500, 'title': '昨夜の寝かしつけ', 'type': 'special', 'target': 'mom', 'days': '0,1,2,3,4,5,6', 'exp': 300, 'gold': 200, 'icon': '💤', 'desc': '子供を寝かしつけた（翌朝申請用）'},
    {'id': 505, 'title': 'お菓子を我慢する', 'type': 'special', 'target': 'mom', 'days': '0,1,2,3,4,5,6', 'exp': 20, 'gold': 30, 'icon': '🙅‍♀️', 'desc': '間食を控えて健康に'},
    {'id': 15, 'title': '洗濯物を干す', 'type': 'special', 'target': 'mom', 'category': 'house', 'difficulty': 'C', 'exp': 50, 'gold': 30, 'icon': '☀️', 'desc': '日光の力で装備を浄化する'},
    {'id': 16, 'title': '洗濯物を畳む', 'type': 'special', 'target': 'mom', 'category': 'house', 'difficulty': 'C', 'exp': 40, 'gold': 30, 'icon': '👕', 'desc': '装備品を整理整頓する'},
    {'id': 17, 'title': '洗濯物をしまう', 'type': 'special', 'target': 'mom', 'category': 'house', 'difficulty': 'D', 'exp': 30, 'gold': 20, 'icon': '🧺', 'desc': 'それぞれのクローゼットへ格納'},


    # ------------------------------------------
    # B-4. 特別：智矢 (Son)
    # ------------------------------------------
    {'id': 30, 'title': '国語プリント (強化中)', 'type': 'special', 'target': 'son', 'category': 'study', 'difficulty': 'C', 'exp': 80, 'gold': 80, 'icon': '📝', 'desc': 'ひらがな特訓。YouTubeより稼げるぞ！'},
    {'id': 31, 'title': '算数プリント (強化中)', 'type': 'special', 'target': 'son', 'category': 'study', 'difficulty': 'C', 'exp': 80, 'gold': 80, 'icon': '🧮', 'desc': '計算マスター。2枚やれば160Gゲット！'},
    {'id': 45, 'title': 'ピアノの練習', 'type': 'special', 'target': 'son', 'category': 'study', 'difficulty': 'C', 'exp': 50, 'gold': 50, 'icon': '🎹', 'desc': '毎日少しずつ上手になろう'},
    {'id': 1009, 'title': '習い事：みらい / ピアノ / あこーでぃおん', 'type': 'special', 'target': 'son', 'category': 'study', 'difficulty': 'B', 'exp': 150, 'gold': 100, 'icon': '🏫', 'desc': '先生とのお勉強やレッスン'},
    {'id': 43, 'title': '一人で30分間 本を読む', 'type': 'special', 'target': 'son', 'category': 'study', 'difficulty': 'C', 'exp': 30, 'gold': 60, 'icon': '📖', 'desc': '本の世界を冒険しよう'},
    {'id': 48, 'title': 'ママのお手伝い', 'type': 'infinite', 'target': 'son', 'category': 'house', 'difficulty': 'D', 'exp': 30, 'gold': 30, 'icon': '🧚', 'desc': 'ママに頼まれたことをやろう'},
    {'id': 49, 'title': 'パパのお手伝い', 'type': 'infinite', 'target': 'son', 'category': 'house', 'difficulty': 'C', 'exp': 50, 'gold': 50, 'icon': '🛠️', 'days': '5,6', 'desc': '週末はパパのサポート任務だ！'},
    {'id': 46, 'title': '休みの日は買い物についてくる', 'type': 'special', 'target': 'son', 'category': 'house', 'difficulty': 'B', 'exp': 100, 'gold': 50, 'icon': '🛒', 'days': '5,6', 'desc': '荷物持ちのサポート任務'},
    {'id': 56, 'title': '自分の部屋の掃除・片付け', 'type': 'special', 'target': 'son', 'category': 'house', 'difficulty': 'B', 'exp': 150, 'gold': 100, 'icon': '🧹', 'days': '5,6', 'desc': '週末は自分の城をきれいにしよう'},
    {'id': 1022, 'title': '騎士のエスコート', 'type': 'infinite', 'target': 'son', 'category': 'moral', 'difficulty': 'C', 'exp': 50, 'gold': 20, 'icon': '🛡️', 'desc': '泣いている妹を慰める、守る'},

    # ------------------------------------------
    # B-5. 特別：涼花 (Daughter)
    # ------------------------------------------
    {'id': 302, 'title': 'トイレでおしっこ成功', 'type': 'infinite', 'target': 'daughter', 'category': 'life', 'difficulty': 'B', 'exp': 50, 'gold': 30, 'icon': '🚽', 'desc': 'トイトレ頑張ろう！'},
]

# ==========================================
# 3. 報酬定義 (Rewards)
# ==========================================
REWARDS = [
    # --- Small (消費型) ---
    {'id': 1, 'title': 'コンビニスイーツ購入権', 'category': 'food', 'cost_gold': 300, 'icon_key': '🍦', 'desc': '頑張った自分へのご褒美デザート'},
    # ID:2 ビール削除

    # YouTube価格は据え置き (価格競争力維持)
    {'id': 10, 'title': 'Youtube (10:00)', 'category': 'service', 'cost_gold': 50, 'icon_key': '📺', 'desc': '好きな動画を見てリフレッシュ', 'target': 'children'},
    {'id': 11, 'title': 'Youtube (30:00)', 'category': 'service', 'cost_gold': 150, 'icon_key': '📺', 'desc': '少し長めの動画も楽しめる', 'target': 'children'},
    {'id': 12, 'title': 'Youtube (60:00)', 'category': 'service', 'cost_gold': 300, 'icon_key': '📺', 'desc': '映画一本分くらいの自由視聴', 'target': 'children'},
    
    {'id': 20, 'title': 'ガチャガチャ 1回', 'category': 'item', 'cost_gold': 400, 'icon_key': '💊', 'desc': '何が出るかな？運試しの1回', 'target': 'children'},
    {'id': 21, 'title': '好きなおやつ 1個', 'category': 'food', 'cost_gold': 100, 'icon_key': '🍪', 'desc': '1枚100円分だよ!!!', 'target': 'children'},

    # --- Medium (体験型) ---
    {'id': 13, 'title': '湯の華廊 チケット', 'category': 'special', 'cost_gold': 1000, 'icon_key': '♨️', 'desc': '広いお風呂で心も体も癒やされる', 'target': 'children'},
    {'id': 30, 'title': 'ローラースケート場チケット', 'category': 'special', 'cost_gold': 1000, 'icon_key': '🛼', 'desc': '風になろう', 'target': 'children'},
    {'id': 31, 'title': 'キッズランドチケット', 'category': 'special', 'cost_gold': 1500, 'icon_key': '🏰', 'desc': 'ピュアキッズでもOK', 'target': 'children'},
    {'id': 23, 'title': '夕飯リクエスト権', 'category': 'service', 'cost_gold': 800, 'icon_key': '🍽️', 'desc': '今夜のメニューはあなたが決める', 'target': 'all'},

    # --- Large (目標型) ---
    {'id': 25, 'title': '回転寿司に行く権', 'category': 'special', 'cost_gold': 2000, 'icon_key': '🍣', 'desc': '回るけど美味しい！パパにお願いしよう', 'target': 'all'},
    {'id': 24, 'title': '好きなおもちゃ (小)', 'category': 'item', 'cost_gold': 5000, 'icon_key': '🤖', 'desc': 'ずっと欲しかったあのおもちゃ', 'target': 'children'},
    {'id': 32, 'title': 'いちご狩り', 'category': 'special', 'cost_gold': 4000, 'icon_key': '🍓', 'desc': '甘くて美味しいいちごをたくさん食べよう！', 'target': 'children'},
    {'id': 33, 'title': 'しいたけ狩り', 'category': 'special', 'cost_gold': 4000, 'icon_key': '🍄', 'desc': '自分で採ったしいたけは最高に美味しいぞ', 'target': 'children'},
    # {'id': 15, 'title': 'スマートウォッチ', 'category': 'item', 'cost_gold': 30000, 'icon_key': '⌚', 'desc': '高性能なハイエンド装備', 'target': 'dad'},

    # --- Premium (夢の報酬) ---
    {'id': 99, 'title': 'ユニバのチケット (ペア)', 'category': 'special', 'cost_gold': 30000, 'icon_key': '🎢', 'desc': 'ハリポタで最高の一日を', 'target': 'mom'},
    {'id': 103, 'title': 'ディズニーのチケット (ペア)', 'category': 'special', 'cost_gold': 60000, 'icon_key': '🐭', 'desc': '夢の国で最高の一日を', 'target': 'mom'},
    # {'id': 101, 'title': '映画のチケット＆半日自由時間', 'category': 'special', 'cost_gold': 20000, 'icon_key': '🎥', 'desc': '好きな映画を見てリフレッシュ'},
    {'id': 100, 'title': 'ホテルに宿泊 (家族旅行)', 'category': 'special', 'cost_gold': 50000, 'icon_key': '🏨', 'desc': '日常を忘れて優雅な滞在', 'target': 'children'},
    {'id': 102, 'title': 'SHARP ヘルシオ ホットクック', 'category': 'item', 'cost_gold': 60000, 'icon_key': '🍲', 'desc': '家事の時間を減らして家族の時間を増やす魔法の鍋', 'target': 'mom'},
    {'id': 104, 'title': '鈴鹿サーキットのチケット', 'category': 'special', 'cost_gold': 30000, 'icon_key': '🏎️', 'desc': '遊園地と車のアトラクションで遊び尽くす！', 'target': 'children'},
    
    # Legend Reward
    {'id': 999, 'title': 'アルハンブラ (Van Cleef & Arpels)', 'category': 'special', 'cost_gold': 1100000, 'icon_key': '🍀', 'desc': '四つ葉のクローバーが象徴する幸運。ママへの究極の感謝状', 'target': 'mom'},

    # 変更: 映画のチケット単体に変更 (2000G)
    {'id': 101, 'title': '映画のチケット', 'category': 'medium', 'cost_gold': 2000, 'icon_key': '🎥', 'desc': '好きな映画を見てリフレッシュ。ポップコーン代は別。', 'target': 'mom'},
    
    # 追加: 半日自由時間 (10000G)
    {'id': 120, 'title': '半日自由時間', 'category': 'special', 'cost_gold': 10000, 'icon_key': '🕊️', 'desc': '4時間程度の完全な自由時間。育児・家事免除。', 'target': 'adults'},

    # 追加: Switchゲーム (350G)
    {'id': 121, 'title': 'Switchのゲーム(45分)', 'category': 'small', 'cost_gold': 350, 'icon_key': '🎮', 'desc': '45分間ゲームで遊べる券。', 'target': 'children'},
]

# ==========================================
# 4. 装備品定義 (Equipment)
# ==========================================
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
# 子供の「敵」を具現化し、倒す（克服する）対象として設定
BOSSES = [
    {'id': 1, 'name': 'ホコリ・スライム', 'hp': 200, 'exp': 100, 'gold': 100, 'icon': '🦠', 'desc': '部屋の隅から生まれた魔物。弱い。'},
    {'id': 2, 'name': 'ヌギッパ・ウルフ', 'hp': 600, 'exp': 300, 'gold': 300, 'icon': '🐺', 'desc': '服を脱ぎっぱなしにする獣。'},
    {'id': 3, 'name': 'ゾンビ・ディッシュ', 'hp': 1500, 'exp': 800, 'gold': 800, 'icon': '🧟', 'desc': '洗い場に溜まった皿の怨念。'},
    
    {'id': 4, 'name': '散らかりドラゴン', 'hp': 3000, 'exp': 2000, 'gold': 2000, 'icon': '🐉', 'desc': '全てを散乱させる巨竜。'},
    {'id': 5, 'name': '魔王カジ・ホウキ', 'hp': 10000, 'exp': 10000, 'gold': 10000, 'icon': '😈', 'desc': '家事の根源にしてラスボス。'},
    
    # 【NEW】生活習慣の敵
    {'id': 6, 'name': 'ネボウ・ノ・魔神', 'hp': 500, 'exp': 200, 'gold': 100, 'icon': '😪', 'desc': '朝の布団から出られない呪いをかける。倒して早起きだ！'},
    {'id': 7, 'name': 'シュクダイ・イーター', 'hp': 800, 'exp': 400, 'gold': 200, 'icon': '📝', 'desc': 'やる気を食べるモンスター。プリント学習でダメージを与えろ！'},
    {'id': 8, 'name': 'ゲンカン・クツ・バラバーラ', 'hp': 300, 'exp': 150, 'gold': 50, 'icon': '👞', 'desc': '靴を脱ぎ散らかす小悪魔。揃えて封印しよう。'}
]
