import sqlite3
import os

# 修正点: configに依存せず、直接絶対パスを指定します
DB_PATH = "/home/masahiro/develop/MY_HOME_SYSTEM/home_system.db"

print(f"Connecting to database: {DB_PATH}")

try:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # カラム追加のSQL実行
    cursor.execute("ALTER TABLE device_records ADD COLUMN battery_level INTEGER;")
    conn.commit()
    print("✅ 成功: 'battery_level' カラムを追加しました。")

except sqlite3.OperationalError as e:
    # 既に追加されている場合のエラーは無視してOK
    if "duplicate column name" in str(e):
        print("ℹ️ 確認: カラムは既に追加されています。")
    else:
        print(f"⚠️ エラー発生: {e}")

except Exception as e:
    print(f"❌ 予期せぬエラー: {e}")

finally:
    if 'conn' in locals():
        conn.close()
        print("🔒 データベース接続を閉じました。")