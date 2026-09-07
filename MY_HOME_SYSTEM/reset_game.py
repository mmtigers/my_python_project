import os
import sys
import logging
import sqlite3
import traceback
from datetime import datetime

import config

# --- 設定 ---
# #186: 以前はCWD相対の"home_system.db"に直接sqlite3.connectしており、他のDB
# アクセス経路(config.SQLITE_DB_PATH = BASE_DIR/home_system.db、環境変数
# SQLITE_DB_PATHで上書き可)と食い違っていた。MY_HOME_SYSTEM/以外のCWDから実行
# するとファイル不在で終了する、あるいは同名ファイルが存在すれば別のDBを誤って
# 操作する、SQLITE_DB_PATH環境変数での差し替え運用時に本番と異なるファイルを
# リセットする、といったリスクがあったため、他のスクリプトと同じconfig.SQLITE_DB_PATH
# を参照するよう統一する。
DB_PATH = config.SQLITE_DB_PATH  # DBファイルパス
# Q-L8(#409): 以前は CWD 相対の "logs" だったため、実行場所によって別のディレクトリに
# ログが作られていた。他スクリプトと同じく config.LOG_DIR を使う。
LOG_DIR = config.LOG_DIR

# 日本語名とDB内のuser_idのマッピング
NAME_MAP = {
    "将博": "dad",
    "春菜": "mom",
    "智矢": "son",
    "涼花": "daughter"
}

# --- ログ設定 ---
log_file = os.path.join(LOG_DIR, f"reset_game_{datetime.now().strftime('%Y%m%d')}.log")


def _setup_logging() -> None:
    """Q-L8(#409): 以前は import 時に basicConfig を実行していたため、テスト等で本モジュールを
    import しただけで logs/ が作られ root logger が乗っ取られていた。main() から呼ぶ。"""
    os.makedirs(LOG_DIR, exist_ok=True)
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

        # Issue #544: 本スクリプトは unified_server とは別プロセスで動くため、サービス層の
        # ユーザー単位ロック(_user_balance_locks)の外で実行される。せめてDB側では
        # BEGIN IMMEDIATE で先に書き込みロックを取り、以下の UPDATE/DELETE を1トランザクションで
        # 確定させる(承認処理の SELECT→絶対値 SET と交錯しても、途中状態が見えないようにする)。
        cursor.execute("BEGIN IMMEDIATE")

        # ★修正箇所: medal_count = 0 を追加
        cursor.execute("""
            UPDATE quest_users 
            SET level = 1, exp = 0, gold = 0, medal_count = 0 
            WHERE user_id = ?
        """, (user_id,))
        
        if cursor.rowcount == 0:
            conn.rollback()
            logging.warning(f"ID '{user_id}' のデータが見つかりませんでした。")
            print(f"⚠️ 注意: データが見つかりませんでした。")
        else:
            # Issue #544: 以前は quest_users のみゼロ化し、approved の quest_history と
            # user_inventory を残していた。そのためリセット後も「本日完了済み」表示が続き、
            # リセット前の履歴を取り消そうとすると #356 のガード(残高 < 付与額)で拒否されて
            # 混乱していた。履歴とインベントリも同じトランザクションで削除する。
            # reward_history(購入ログ)は残高に影響しない監査用の記録のため対象外。
            cursor.execute("DELETE FROM quest_history WHERE user_id = ?", (user_id,))
            deleted_history = cursor.rowcount
            cursor.execute("DELETE FROM user_inventory WHERE user_id = ?", (user_id,))
            deleted_inventory = cursor.rowcount
            conn.commit()
            logging.info(
                f"DB更新成功: {user_label} のデータをリセットしました。"
                f"(quest_history {deleted_history}件, user_inventory {deleted_inventory}件を削除)"
            )
            # メッセージにもメダルリセットを含める
            print(
                f"\n✅ {user_label} さんのデータをリセットしました "
                f"(Level=1, Exp=0, Gold=0, Medal=0, クエスト履歴 {deleted_history}件削除, "
                f"インベントリ {deleted_inventory}件削除)。"
            )
        
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
    _setup_logging()
    logging.info("スクリプト起動: ユーザー選択モード")
    
    users_info = fetch_users()
    
    if not users_info:
        logging.error("ユーザー情報が取得できませんでした。DBを確認してください。")
        print("ユーザー情報が取得できませんでした。")
        sys.exit(1)

    selected = select_user_interactive(users_info)
    if not selected:
        sys.exit(0)
    
    # Issue #544: 履歴・インベントリも削除するようになったため、確認文でその旨を明示する
    confirm = input(
        f"\n本当に '{selected['label']}' のデータをリセットしますか？"
        " (Level/Exp/Gold/Medal を初期化し、クエスト履歴とインベントリを全削除します) (y/n): "
    ).strip().lower()
    if confirm != 'y':
        logging.info("ユーザーにより操作がキャンセルされました。")
        print("キャンセルしました。")
        sys.exit(0)

    reset_user_data(selected)

if __name__ == "__main__":
    main()