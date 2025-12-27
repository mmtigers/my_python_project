import sqlite3
import config

# モックデータの内容をDBに移行
USERS = [
    ('kid1', '智矢', 80),
    ('kid2', '涼花', 40),
    ('mom', 'ママ', 350),
    ('dad', 'パパ', 120),
]

TASKS = [
    ('kid1', 'おもちゃを片付ける', 'Gamepad2', 10),
    ('kid1', '食器を下げる', 'Utensils', 20),
    ('kid1', 'お着替えする', 'Shirt', 15),
    ('kid2', 'はみがき', 'Smile', 50),
    ('kid2', 'パジャマきる', 'Moon', 30),
    ('dad', 'ゴミ出し', 'Trash2', 50),
    ('mom', '寝かしつけ', 'BedDouble', 100),
]

REWARDS = [
    ('YouTube 30分', 100, '📺'),
    ('おやつ1つ', 50, '🍪'),
    ('公園にいく', 200, '🛝'),
    ('ゲーム 30分', 150, '🎮'),
    ('スペシャルガチャ', 500, '🎁'),
]

def seed_data():
    conn = sqlite3.connect(config.SQLITE_DB_PATH)
    cur = conn.cursor()
    
    print("🌱 データを投入しています...")

    # 既存データをクリア（開発用）
    cur.execute("DELETE FROM quest_users")
    cur.execute("DELETE FROM quest_tasks")
    cur.execute("DELETE FROM quest_rewards")

    # ユーザー
    cur.executemany("INSERT INTO quest_users (id, name, current_points) VALUES (?, ?, ?)", USERS)
    
    # タスク
    cur.executemany("INSERT INTO quest_tasks (target_user_id, title, icon_name, points) VALUES (?, ?, ?, ?)", TASKS)

    # リワード
    cur.executemany("INSERT INTO quest_rewards (title, cost, icon_char) VALUES (?, ?, ?)", REWARDS)

    conn.commit()
    conn.close()
    print("✅ 完了しました！")

if __name__ == "__main__":
    seed_data()