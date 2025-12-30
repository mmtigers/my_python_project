import common
import sqlite3
import config

def check_rewards():
    print(f"🔍 Checking Database: {config.SQLITE_DB_PATH}")
    print("--------------------------------------------------")
    
    try:
        with common.get_db_cursor() as cur:
            # 1. 報酬マスタの確認
            rows = cur.execute("SELECT * FROM reward_master").fetchall()
            
            if not rows:
                print("⚠️  テーブル 'reward_master' は空です！")
                print("   -> quest_data.py の REWARDS が空か、読み込めていません。")
            else:
                print(f"✅ 'reward_master' に {len(rows)} 件のデータが見つかりました:")
                print(f"{'ID':<4} | {'Title':<20} | {'Cost':<6} | {'Icon'}")
                print("-" * 50)
                for row in rows:
                    r = dict(row)
                    print(f"{r['reward_id']:<4} | {r['title']:<20} | {r['cost_gold']:<6} | {r.get('icon_key', '')}")

            print("\n--------------------------------------------------")
            
            # 2. ユーザー所持金の確認 (念のため)
            users = cur.execute("SELECT user_id, name, gold FROM quest_users").fetchall()
            print("💰 ユーザー所持金:")
            for u in users:
                print(f" - {u['name']}: {u['gold']} G")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    check_rewards()