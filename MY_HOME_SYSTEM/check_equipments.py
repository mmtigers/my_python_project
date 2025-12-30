# MY_HOME_SYSTEM/check_equipments.py
import common

def check():
    print("🔍 Checking Equipment Master Table...")
    try:
        with common.get_db_cursor() as cur:
            rows = cur.execute("SELECT * FROM equipment_master").fetchall()
            if not rows:
                print("⚠️ テーブルは空です。sync_masterを実行してください。")
            else:
                print(f"✅ {len(rows)} 個の装備が見つかりました！")
                for r in rows:
                    print(f" - {r['name']} ({r['type']}): {r['cost_gold']}G")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    check()