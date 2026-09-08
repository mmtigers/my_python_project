import os
import sys
import logging
import sqlite3
import traceback
from datetime import datetime

import requests

import config

# --- 設定 ---
# #186: 以前はCWD相対の"home_system.db"に直接sqlite3.connectしており、他のDB
# アクセス経路(config.SQLITE_DB_PATH = BASE_DIR/home_system.db、環境変数
# SQLITE_DB_PATHで上書き可)と食い違っていた。MY_HOME_SYSTEM/以外のCWDから実行
# するとファイル不在で終了する、あるいは同名ファイルが存在すれば別のDBを誤って
# 操作する、SQLITE_DB_PATH環境変数での差し替え運用時に本番と異なるファイルを
# リセットする、といったリスクがあったため、他のスクリプトと同じconfig.SQLITE_DB_PATH
# を参照するよう統一する。
# #547: リセット自体はAPI経由になったが、対象ユーザー一覧の表示(読み取りのみで
# 承認処理との交錯の危険がない)は引き続きこのDBを直接読む。
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

# Issue #547: 本スクリプトはunified_serverとは別プロセスで動くため、サービス層の
# ユーザー単位ロック(_user_balance_locks)を稼働中のサーバーと共有できない。以前は
# BEGIN IMMEDIATEでDB側の原子性のみを確保して直接quest_users/quest_history/
# user_inventoryを書き換えていたが、承認処理(process_approve_quest等のSELECT→
# Pythonで計算→絶対値SET)と交錯すると、承認側のUPDATEがリセット結果を上書きしうる
# 欠陥が残っていた。リセット処理自体はサーバーのAPI(routers/quest_router.py の
# POST /api/quest/admin/reset_user、services/quest/user_service.py の
# UserService.reset_user_data。承認等と同じ_get_user_balance_lockの中で実行される)
# を呼ぶ薄いクライアントに置き換え、サーバー側のロックに参加させることで解消する。
# サーバー未起動時に直接DBを書き換えるフォールバックは持たない(このIssueの目的は
# 稼働中サーバーとの交錯を防ぐことであり、フォールバックを残すと同じ欠陥のある
# コードパスを温存することになるため。サーバーを起動してから実行することを促す)。
# ベースURLはconfig.RESET_GAME_API_BASE_URL(環境変数RESET_GAME_API_BASE_URLで上書き可、
# 未設定時はループバックアドレス)を参照する。
RESET_USER_API_PATH = "/api/quest/admin/reset_user"
RESET_API_TIMEOUT_SECONDS = 10

# quest_users.roleの値。リセットAPI呼び出し時のadmin_id(role_adultであることが
# サーバー側で必須)を自動選択するために使う。services/quest/locks.pyのROLE_ADULTと
# 同じ文字列だが、本スクリプトはサービス層をimportしない独立した対話スクリプトの
# ため、値をここに複製している。
ROLE_ADULT = "role_adult"

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
    DBからユーザー情報を取得し、表示用のリストを作成する。

    #547: role列も取得する。リセットAPI呼び出し時にadmin_id(role_adultの
    ユーザー)を自動選択するために必要。
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT user_id, name, role FROM quest_users")
        rows = cursor.fetchall()

        users_info = []
        for row in rows:
            u_id = row['user_id']
            u_name = row['name']
            display_name = u_name if u_name else u_id
            users_info.append({"id": u_id, "name": display_name, "role": row['role']})

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

def _find_admin_user_id(users_info):
    """role_adultの最初のユーザーIDを返す。見つからなければNoneを返す(#547)。"""
    for u in users_info:
        if u.get("role") == ROLE_ADULT:
            return u["id"]
    return None


def reset_user_data(target_user, users_info, base_url=None):
    """
    指定されたユーザーのゲームデータを、サーバーのリセットAPI経由でリセットする。

    #547: admin_id(role_adultであることがサーバー側で必須)は、users_info
    (fetch_usersの戻り値)からrole_adultの最初のユーザーを自動選択する。
    本スクリプトは家庭内の管理者が端末に直接アクセスして実行する対話スクリプトの
    ため、実行者を別途選択させる入力は追加しない。
    """
    user_id = target_user['db_id']
    user_label = target_user['label']

    if base_url is None:
        # 呼び出し時点のconfig.RESET_GAME_API_BASE_URLを見る(defaultをdef時点で
        # 束縛すると、テストでのmonkeypatch.setattr(config, ...)が反映されない)。
        base_url = config.RESET_GAME_API_BASE_URL

    admin_id = _find_admin_user_id(users_info)
    if not admin_id:
        logging.error("role_adultのユーザーが見つからないため、リセットAPIを呼び出せません。")
        print("\n❌ 管理者(role_adult)のユーザーが見つかりませんでした。")
        sys.exit(1)

    url = f"{base_url}{RESET_USER_API_PATH}"
    logging.info(f"ユーザー '{user_label}' (ID: {user_id}) のリセットをAPI({url})経由で実行します。")

    try:
        resp = requests.post(
            url,
            json={"admin_id": admin_id, "target_user_id": user_id},
            timeout=RESET_API_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as e:
        logging.error(f"リセットAPI呼び出しに失敗しました: {e}")
        print(
            "\n❌ サーバーに接続できませんでした。unified_serverが起動しているか確認してください。"
            f"\n   詳細はログを確認してください: {log_file}"
        )
        sys.exit(1)

    if resp.status_code == 404:
        logging.warning(f"ID '{user_id}' のデータが見つかりませんでした。")
        print("⚠️ 注意: データが見つかりませんでした。")
        return

    if not resp.ok:
        logging.error(f"リセットAPIがエラーを返しました: status={resp.status_code}, body={resp.text}")
        print(f"\n❌ エラーが発生しました({resp.status_code})。ログを確認してください: {log_file}")
        sys.exit(1)

    body = resp.json()
    deleted_history = body.get("deletedHistoryCount", 0)
    deleted_inventory = body.get("deletedInventoryCount", 0)
    logging.info(
        f"リセット成功: {user_label} のデータをリセットしました。"
        f"(quest_history {deleted_history}件, user_inventory {deleted_inventory}件を削除)"
    )
    print(
        f"\n✅ {user_label} さんのデータをリセットしました "
        f"(Level=1, Exp=0, Gold=0, Medal=0, クエスト履歴 {deleted_history}件削除, "
        f"インベントリ {deleted_inventory}件削除)。"
    )

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

    reset_user_data(selected, users_info)

if __name__ == "__main__":
    main()