import os
import sys
import logging
import sqlite3
import traceback
from datetime import datetime

# --- 設定 ---
DB_PATH = "home_system.db"  # DBファイルパス
LOG_DIR = "logs"

# 日本語名とDB内のuser_idのマッピング
NAME_MAP = {
    "将博": "dad",
    "春菜": "mom",
    "智矢": "son",
    "涼花": "daughter"
}

# --- ログ設定 ---
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, f"reset_game_{datetime.now().strftime('%Y%m%d')}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

def get_db_connection():
    """データベース接続を取得する"""
    if not os.path.exists(DB_PATH):
        logging.error(f"DBファイルが見つかりません: {DB_PATH}")
        print(f"❌ DBファイル '{DB_PATH}' が見つかりません。")
        sys.exit(1)
        
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logging.error(f"DB接続エラー: {e}")
        raise

def fetch_users():
    """
    DBからユーザー情報を取得し、表示用のリストを作成する
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT user_id, name FROM quest_users")
        rows = cursor.fetchall()
        
        users_info = []
        for row in rows:
            u_id = row['user_id']
            u_name = row['name']
            display_name = u_name if u_name else u_id
            users_info.append({"id": u_id, "name": display_name})
            
        return users_info

    except Exception as e:
        logging.error(f"ユーザーリスト取得失敗: {e}")
        logging.debug(traceback.format_exc())
        return []
    finally:
        if conn:
            conn.close()

def select_user_interactive(users_info):
    """
    ユーザーにリストを表示し、選択させる
    """
    print("\n--- リセット対象を選択してください ---")
    
    display_candidates = []
    
    # NAME_MAPにある名前を優先表示
    db_user_ids = [u['id'] for u in users_info]
    
    for jp_name, db_id in NAME_MAP.items():
        if db_id in db_user_ids:
            display_candidates.append({"label": jp_name, "db_id": db_id})
    
    # マップにないその他のユーザーも追加
    mapped_ids = NAME_MAP.values()
    for u in users_info:
        if u['id'] not in mapped_ids:
             display_candidates.append({"label": f"{u['name']} ({u['id']})", "db_id": u['id']})

    if not display_candidates:
        print("リセット可能なユーザーが見つかりませんでした。")
        return None

    for index, user in enumerate(display_candidates):
        print(f"{index + 1}. {user['label']}")
    print("q. キャンセル")
    print("--------------------------------------")

    while True:
        choice = input("番号を入力してください: ").strip()
        
        if choice.lower() == 'q':
            print("操作をキャンセルしました。")
            sys.exit(0)
            
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(display_candidates):
                return display_candidates[idx]
        
        print("無効な入力です。リストの番号を入力してください。")

def reset_user_data(target_user):
    """
    指定されたユーザーのゲームデータをリセットする
    """
    user_id = target_user['db_id']
    user_label = target_user['label']
    
    logging.info(f"ユーザー '{user_label}' (ID: {user_id}) のリセット処理を開始します。")
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # ★修正箇所: medal_count = 0 を追加
        cursor.execute("""
            UPDATE quest_users 
            SET level = 1, exp = 0, gold = 0, medal_count = 0 
            WHERE user_id = ?
        """, (user_id,))
        
        if cursor.rowcount == 0:
            logging.warning(f"ID '{user_id}' のデータが見つかりませんでした。")
            print(f"⚠️ 注意: データが見つかりませんでした。")
        else:
            conn.commit()
            logging.info(f"DB更新成功: {user_label} のデータをリセットしました。")
            # メッセージにもメダルリセットを含める
            print(f"\n✅ {user_label} さんのデータをリセットしました (Level=1, Exp=0, Gold=0, Medal=0)。")
        
    except Exception as e:
        error_msg = f"リセット処理中にエラーが発生: {str(e)}"
        logging.error(error_msg)
        logging.error(traceback.format_exc())
        print(f"\n❌ エラーが発生しました。ログを確認してください: {log_file}")
        sys.exit(1)
    finally:
        if conn:
            conn.close()

def main():
    logging.info("スクリプト起動: ユーザー選択モード")
    
    users_info = fetch_users()
    
    if not users_info:
        logging.error("ユーザー情報が取得できませんでした。DBを確認してください。")
        print("ユーザー情報が取得できませんでした。")
        sys.exit(1)

    selected = select_user_interactive(users_info)
    if not selected:
        sys.exit(0)
    
    confirm = input(f"\n本当に '{selected['label']}' のデータをリセットしますか？ (y/n): ").strip().lower()
    if confirm != 'y':
        logging.info("ユーザーにより操作がキャンセルされました。")
        print("キャンセルしました。")
        sys.exit(0)

    reset_user_data(selected)

if __name__ == "__main__":
    main()