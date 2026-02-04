# MY_HOME_SYSTEM/update_schema.py
import os
import sys
import sqlite3

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import config

def update_db_schema():
    print("🛠️ Database Schema Update...")
    conn = sqlite3.connect(config.SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row  # カラム名アクセス用（確認のため）
    cur = conn.cursor()
    
    # ---------------------------------------------------------
    # 1. quest_history: status カラム追加 (既存の修正)
    # ---------------------------------------------------------
    try:
        cur.execute("SELECT status FROM quest_history LIMIT 1")
    except sqlite3.OperationalError:
        print("ℹ️ 'status' column missing in quest_history. Adding...")
        try:
            cur.execute("ALTER TABLE quest_history ADD COLUMN status TEXT DEFAULT 'approved'")
            print("✅ Added 'status' column to quest_history.")
        except Exception as e:
            print(f"❌ Failed to add 'status' column: {e}")

    # ---------------------------------------------------------
    # 2. Food Table: menu_category 等の追加 (今回のエラー対応)
    # ---------------------------------------------------------
    # configからテーブル名を取得
    table_food = config.SQLITE_TABLE_FOOD
    print(f"🔍 Checking table: {table_food}")

    # menu_category の確認と追加
    try:
        cur.execute(f"SELECT menu_category FROM {table_food} LIMIT 1")
    except sqlite3.OperationalError:
        print(f"⚠️ 'menu_category' column missing in {table_food}. Adding...")
        try:
            cur.execute(f"ALTER TABLE {table_food} ADD COLUMN menu_category TEXT")
            print(f"✅ Added 'menu_category' column to {table_food}.")
        except Exception as e:
            print(f"❌ Failed to add 'menu_category': {e}")

    # meal_time_category も同時に追加された可能性があるため念のため確認
    try:
        cur.execute(f"SELECT meal_time_category FROM {table_food} LIMIT 1")
    except sqlite3.OperationalError:
        print(f"⚠️ 'meal_time_category' column missing in {table_food}. Adding...")
        try:
            cur.execute(f"ALTER TABLE {table_food} ADD COLUMN meal_time_category TEXT")
            print(f"✅ Added 'meal_time_category' column to {table_food}.")
        except Exception as e:
            print(f"❌ Failed to add 'meal_time_category': {e}")

    conn.commit()
    conn.close()
    print("🏁 Update finished.")

if __name__ == "__main__":
    update_db_schema()