# check_db_quest.py
import sqlite3
import config

def check_quest_data():
    print(f"Checking DB: {config.SQLITE_DB_PATH}")
    try:
        conn = sqlite3.connect(config.SQLITE_DB_PATH)
        cur = conn.cursor()
        
        # クエストID 92 の情報を取得
        cur.execute("SELECT quest_id, title, end_date FROM quest_master WHERE quest_id = 92")
        row = cur.fetchone()
        
        if row:
            print(f"Found Quest: {row}")
            print(f"ID: {row[0]}")
            print(f"Title: {row[1]}")
            print(f"End Date (DB Value): '{row[2]}'")  # シングルクォートで囲って表示
            
            if row[2] == "2026-1-1":
                print("❌ 判定: データが古い形式のままです（ゼロ埋めなし）。更新処理が動いていません。")
            elif row[2] == "2026-01-01":
                print("✅ 判定: データは正しく更新されています（ゼロ埋めあり）。")
            else:
                print(f"❓ 判定: 想定外の値です: {row[2]}")
        else:
            print("❌ Quest ID 92 not found in DB.")
            
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_quest_data()