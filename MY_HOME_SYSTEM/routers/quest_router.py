# MY_HOME_SYSTEM/routers/quest_router.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import sqlite3
import datetime
import math
import config
import common

router = APIRouter()
logger = common.setup_logging("quest_router")

# --- Pydantic Models (リクエスト/レスポンス定義) ---
class UserAction(BaseModel):
    user_id: str

class QuestAction(BaseModel):
    user_id: str
    quest_id: int

class RewardAction(BaseModel):
    user_id: str
    reward_id: int

class HistoryAction(BaseModel):
    user_id: str
    history_id: int

# --- Helper Functions ---
def get_db_connection():
    conn = sqlite3.connect(config.SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def calculate_next_level_exp(level):
    return math.floor(100 * math.pow(1.2, level - 1))

# --- 初期データ投入用 (DBが空の場合のみ実行) ---
@router.post("/seed")
def seed_data():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # ユーザー
    users = [
        ('dad', 'まさひろ', '勇者', 1, 0, 50),
        ('mom', 'はるな', '魔法使い', 1, 0, 150)
    ]
    for u in users:
        cur.execute("INSERT OR IGNORE INTO quest_users (user_id, name, job_class, level, exp, gold) VALUES (?, ?, ?, ?, ?, ?)", u)

    # クエスト (フロントエンドの定数と同じ内容)
    quests = [
        (1, 'お風呂掃除', 'daily', 20, 10, '💧', None),
        (2, '食器洗い', 'daily', 15, 5, '🍽️', None),
        (3, '洗濯干し', 'daily', 15, 5, '👕', None),
        (4, '燃えるゴミ出し', 'weekly', 30, 15, '🔥', '1,4'),
        (5, 'プラゴミ出し', 'weekly', 30, 15, '♻️', '3'),
        (6, '週末の買い出し', 'weekly', 50, 30, '🛒', '0,6'),
        (7, '寝かしつけ', 'daily', 40, 0, '💤', None),
        (8, '保育園送り', 'daily', 25, 10, '🚲', '1,2,3,4,5'),
    ]
    for q in quests:
        cur.execute("INSERT OR IGNORE INTO quest_master (quest_id, title, description, exp_gain, gold_gain, icon_key, day_of_week) VALUES (?, ?, ?, ?, ?, ?, ?)", q)

    # 報酬
    rewards = [
        (101, '高級アイス', 'food', 100, '🍨'),
        (102, 'ビール/お酒', 'food', 150, '🍺'),
        (103, 'マッサージ券', 'service', 500, '💆'),
        (201, 'はやての靴', 'equip', 3000, '👟'),
        (202, '勇者のゲーム', 'equip', 5000, '🎮'),
        (203, '時の砂時計', 'special', 1000, '⏳'),
        (204, '伝説の包丁', 'equip', 2500, '🔪'),
    ]
    for r in rewards:
        cur.execute("INSERT OR IGNORE INTO reward_master (reward_id, title, category, cost_gold, icon_key) VALUES (?, ?, ?, ?, ?)", r)
        
    conn.commit()
    conn.close()
    return {"status": "seeded"}

# --- Endpoints ---

@router.get("/data")
def get_all_data():
    """アプリ起動時に必要な全データを返す"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Users
    users = []
    for row in cur.execute("SELECT * FROM quest_users"):
        u = dict(row)
        u['nextLevelExp'] = calculate_next_level_exp(u['level'])
        # 簡易的に inventory を取得 (報酬履歴から装備品のみ抽出)
        inv_rows = cur.execute("""
            SELECT r.* FROM reward_history rh 
            JOIN reward_master r ON rh.reward_id = r.reward_id 
            WHERE rh.user_id = ? AND r.category = 'equip'
        """, (u['user_id'],)).fetchall()
        u['inventory'] = [dict(r) for r in inv_rows]
        
        # UI表示用の avatar (DBにないので補完)
        if u['user_id'] == 'dad': u['avatar'] = '⚔️'
        elif u['user_id'] == 'mom': u['avatar'] = '🪄'
        else: u['avatar'] = '🙂'
        
        # HP (簡易計算: level * 20 + 5)
        u['maxHp'] = u['level'] * 20 + 5
        u['hp'] = u['maxHp'] # 常に満タン
        
        users.append(u)

    # Quests
    quests = [dict(row) for row in cur.execute("SELECT * FROM quest_master")]
    for q in quests:
        # DBの '1,4' 文字列を配列 [1, 4] に変換
        if q['day_of_week']:
            q['days'] = [int(d) for d in q['day_of_week'].split(',')]
        else:
            q['days'] = None
        q['icon'] = q['icon_key'] # フロントエンド互換

    # Rewards
    rewards = [dict(row) for row in cur.execute("SELECT * FROM reward_master")]
    for r in rewards:
        r['icon'] = r['icon_key']

    # History (本日のクエスト完了状況)
    today = datetime.date.today().isoformat()
    completed = [dict(row) for row in cur.execute(
        "SELECT * FROM quest_history WHERE date(completed_at) = ?", (today,)
    )]
    
    # Logs (冒険の書: 最近の50件)
    logs = []
    # クエスト履歴
    q_logs = cur.execute("SELECT id, user_id, quest_title as title, 'quest' as type, completed_at as ts FROM quest_history ORDER BY id DESC LIMIT 50").fetchall()
    # 報酬履歴
    r_logs = cur.execute("SELECT id, user_id, reward_title as title, 'reward' as type, redeemed_at as ts FROM reward_history ORDER BY id DESC LIMIT 50").fetchall()
    
    # 統合してソート
    all_logs = sorted(q_logs + r_logs, key=lambda x: x['ts'], reverse=True)[:50]
    
    # 名前解決して整形
    user_map = {u['user_id']: u['name'] for u in users}
    
    formatted_logs = []
    for l in all_logs:
        name = user_map.get(l['user_id'], '誰か')
        ts_str = l['ts'] # YYYY-MM-DD HH:MM:SS
        date_str = ts_str.split(' ')[0]
        
        text = ""
        if l['type'] == 'quest':
            text = f"{name}は {l['title']} をクリアした！"
        else:
            text = f"{name}は {l['title']} を手に入れた！"
            
        formatted_logs.append({
            "id": f"{l['type']}_{l['id']}",
            "text": text,
            "dateStr": date_str,
            "timestamp": ts_str
        })

    conn.close()
    
    return {
        "users": users,
        "quests": quests,
        "rewards": rewards,
        "completedQuests": completed, # フロントエンドの判定用
        "logs": formatted_logs
    }

@router.post("/quest/complete")
def complete_quest(action: QuestAction):
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. クエスト情報取得
    quest = cur.execute("SELECT * FROM quest_master WHERE quest_id = ?", (action.quest_id,)).fetchone()
    if not quest:
        conn.close()
        raise HTTPException(status_code=404, detail="Quest not found")
        
    # 2. ユーザー情報取得
    user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (action.user_id,)).fetchone()
    if not user:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
        
    current_level = user['level']
    current_exp = user['exp'] + quest['exp_gain']
    current_gold = user['gold'] + quest['gold_gain']
    
    # 3. レベルアップ判定
    leveled_up = False
    next_exp = calculate_next_level_exp(current_level)
    
    while current_exp >= next_exp:
        current_exp -= next_exp
        current_level += 1
        leveled_up = True
        next_exp = calculate_next_level_exp(current_level)
        
    # 4. 更新
    cur.execute("""
        UPDATE quest_users 
        SET level = ?, exp = ?, gold = ?, updated_at = ? 
        WHERE user_id = ?
    """, (current_level, current_exp, current_gold, datetime.datetime.now(), action.user_id))
    
    # 5. 履歴保存
    cur.execute("""
        INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (action.user_id, quest['quest_id'], quest['title'], quest['exp_gain'], quest['gold_gain'], datetime.datetime.now()))
    
    conn.commit()
    conn.close()
    
    return {
        "status": "success",
        "leveledUp": leveled_up,
        "newLevel": current_level,
        "earnedGold": quest['gold_gain'],
        "earnedExp": quest['exp_gain']
    }

@router.post("/quest/cancel")
def cancel_quest(action: HistoryAction):
    """間違えて完了したクエストを取り消す"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 履歴取得
    hist = cur.execute("SELECT * FROM quest_history WHERE id = ?", (action.history_id,)).fetchone()
    if not hist:
        conn.close()
        raise HTTPException(status_code=404, detail="History not found")
        
    if hist['user_id'] != action.user_id:
        conn.close()
        raise HTTPException(status_code=403, detail="User mismatch")

    # ユーザー情報
    user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (action.user_id,)).fetchone()
    
    # 減算処理 (レベルダウンも考慮)
    new_gold = max(0, user['gold'] - hist['gold_earned'])
    new_exp = user['exp'] - hist['exp_earned']
    new_level = user['level']
    
    while new_exp < 0 and new_level > 1:
        new_level -= 1
        prev_level_max = calculate_next_level_exp(new_level)
        new_exp += prev_level_max
        
    if new_exp < 0: new_exp = 0 # Lv1でマイナスなら0丸め
    
    # 更新
    cur.execute("UPDATE quest_users SET level=?, exp=?, gold=? WHERE user_id=?", 
                (new_level, new_exp, new_gold, action.user_id))
    
    # 履歴削除
    cur.execute("DELETE FROM quest_history WHERE id = ?", (action.history_id,))
    
    conn.commit()
    conn.close()
    return {"status": "cancelled"}

@router.post("/reward/purchase")
def purchase_reward(action: RewardAction):
    conn = get_db_connection()
    cur = conn.cursor()
    
    reward = cur.execute("SELECT * FROM reward_master WHERE reward_id = ?", (action.reward_id,)).fetchone()
    user = cur.execute("SELECT * FROM quest_users WHERE user_id = ?", (action.user_id,)).fetchone()
    
    if not reward or not user:
        conn.close()
        raise HTTPException(status_code=404, detail="Not found")
        
    if user['gold'] < reward['cost_gold']:
        conn.close()
        raise HTTPException(status_code=400, detail="Not enough gold")
        
    # 購入処理
    new_gold = user['gold'] - reward['cost_gold']
    cur.execute("UPDATE quest_users SET gold = ? WHERE user_id = ?", (new_gold, action.user_id))
    
    cur.execute("""
        INSERT INTO reward_history (user_id, reward_id, reward_title, cost_gold, redeemed_at)
        VALUES (?, ?, ?, ?, ?)
    """, (action.user_id, reward['reward_id'], reward['title'], reward['cost_gold'], datetime.datetime.now()))
    
    conn.commit()
    conn.close()
    
    return {"status": "purchased", "newGold": new_gold}