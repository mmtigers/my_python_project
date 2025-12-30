import sys
import common  # プロジェクト共通モジュール
from quest_data import QUESTS, REWARDS, USERS  # マスターデータ

# ロガー設定
logger = common.setup_logging("strict_sync")

def sync_quests(cur):
    """クエスト定義の完全同期 (不要なデータは削除)"""
    logger.info("--- Syncing Quests (Strict Mode) ---")
    
    # 1. マスターデータ内のIDリストを取得
    master_ids = [q['id'] for q in QUESTS]
    
    # 2. マスターに存在しない古いデータをDBから削除 (Clean Up)
    if master_ids:
        placeholders = ','.join(['?'] * len(master_ids))
        # テーブル名: quest_master, 主キー: quest_id
        sql_delete = f"DELETE FROM quest_master WHERE quest_id NOT IN ({placeholders})"
        cur.execute(sql_delete, master_ids)
        logger.info(f"Deleted obsolete quests: {cur.rowcount} rows")
    else:
        cur.execute("DELETE FROM quest_master")
        logger.info("Deleted ALL quests (Master is empty)")

    # 3. マスターデータをUpsert
    for q in QUESTS:
        # キー名のゆれを吸収 (exp/exp_gain, gold/gold_gain, icon/icon_key)
        exp_val = q.get('exp_gain', q.get('exp', 0))
        gold_val = q.get('gold_gain', q.get('gold', 0))
        icon_val = q.get('icon_key', q.get('icon', '📝'))
        
        # init_unified_db.py の定義に合わせてカラムを指定
        cur.execute("""
            INSERT INTO quest_master (
                quest_id, title, quest_type, target_user, 
                exp_gain, gold_gain, icon_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(quest_id) DO UPDATE SET
                title = excluded.title,
                quest_type = excluded.quest_type,
                target_user = excluded.target_user,
                exp_gain = excluded.exp_gain,
                gold_gain = excluded.gold_gain,
                icon_key = excluded.icon_key
        """, (
            q['id'],
            q['title'],
            q['type'],
            q['target'],
            exp_val,
            gold_val,
            icon_val
        ))
    logger.info(f"Upserted {len(QUESTS)} quests.")

def sync_rewards(cur):
    """報酬データの完全同期"""
    logger.info("--- Syncing Rewards ---")
    master_ids = [r['id'] for r in REWARDS]
    
    # 削除
    if master_ids:
        placeholders = ','.join(['?'] * len(master_ids))
        cur.execute(f"DELETE FROM reward_master WHERE reward_id NOT IN ({placeholders})", master_ids)
    
    # Upsert
    for r in REWARDS:
        # ★ここを修正: cost または cost_gold どちらでも取得できるようにする
        cost_val = r.get('cost_gold', r.get('cost', 0))
        icon_val = r.get('icon_key', r.get('icon', '🎁'))
        
        cur.execute("""
            INSERT INTO reward_master (reward_id, title, category, cost_gold, icon_key)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(reward_id) DO UPDATE SET
                title = excluded.title,
                cost_gold = excluded.cost_gold,
                icon_key = excluded.icon_key
        """, (r['id'], r['title'], r['category'], cost_val, icon_val))
    logger.info(f"Upserted {len(REWARDS)} rewards.")

def main():
    logger.info("Starting Strict Master Data Sync (v2)...")
    
    try:
        # commonモジュールのDB接続を使用（自動コミット）
        with common.get_db_cursor(commit=True) as cur:
            sync_quests(cur)
            sync_rewards(cur)
            
        logger.info("✅ Sync completed successfully.")
        
    except Exception as e:
        logger.error(f"❌ Sync failed: {e}")
        # 詳細なエラー情報を出すためにtracebackを表示しても良いが、まずはメッセージのみ
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()