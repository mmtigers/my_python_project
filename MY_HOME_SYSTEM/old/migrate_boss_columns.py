import sqlite3
import config
import common
from datetime import datetime, timedelta

# ロガー設定
logger = common.setup_logging("migration")

def migrate_party_state():
    logger.info("🛡️ party_stateテーブルのマイグレーションを開始します...")
    
    with common.get_db_cursor(commit=True) as cur:
        # 現在のカラム情報を取得
        cur.execute("PRAGMA table_info(party_state)")
        columns = [info['name'] for info in cur.fetchall()]
        
        # 追加したいカラムとその型・デフォルト値の定義
        new_columns = {
            "max_hp": "INTEGER DEFAULT 1000",
            "week_start_date": "TEXT DEFAULT ''",
            "is_defeated": "INTEGER DEFAULT 0",
            "total_damage": "INTEGER DEFAULT 0"
        }
        
        for col_name, col_def in new_columns.items():
            if col_name not in columns:
                try:
                    alter_query = f"ALTER TABLE party_state ADD COLUMN {col_name} {col_def}"
                    cur.execute(alter_query)
                    logger.info(f"✅ カラム追加: {col_name}")
                except Exception as e:
                    logger.error(f"❌ カラム追加失敗 ({col_name}): {e}")
            else:
                logger.info(f"ℹ️ カラム存在済み: {col_name}")

        # 初期データの整合性チェック（レコードがない場合は作成）
        cur.execute("SELECT * FROM party_state WHERE id = 1")
        if not cur.fetchone():
            logger.info("⚠️ party_stateのレコードが存在しません。初期レコードを作成します。")
            today = datetime.now().date()
            monday = today - timedelta(days=today.weekday())
            cur.execute("""
                INSERT INTO party_state (id, current_boss_id, current_hp, max_hp, week_start_date, is_defeated, total_damage)
                VALUES (1, 1, 1000, 1000, ?, 0, 0)
            """, (str(monday),))

    logger.info("🎉 マイグレーション完了")

if __name__ == "__main__":
    migrate_party_state()