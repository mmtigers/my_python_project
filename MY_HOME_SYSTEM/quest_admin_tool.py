import sqlite3
import os
import sys
import datetime

# カレントディレクトリのモジュール(game_logic)を読み込めるようにパスを追加
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from game_logic import GameLogic
except ImportError:
    print("⚠️ 警告: game_logic.py が見つかりません。経験値のレベル計算が正しく動作しない可能性があります。")
    # フォールバック用の簡易ロジック
    class GameLogic:
        @staticmethod
        def calc_level_progress(lvl, exp, add): return lvl, exp + add, False
        @staticmethod
        def calc_level_down(lvl, exp, rem): return lvl, max(0, exp - rem)

# 設定
DB_PATH = "home_system.db"

def get_db_connection():
    if not os.path.exists(DB_PATH):
        print(f"❌ エラー: データベース ({DB_PATH}) が見つかりません。MY_HOME_SYSTEMフォルダ内で実行してください。")
        sys.exit(1)
    return sqlite3.connect(DB_PATH)

def select_player(cursor):
    """① プレイヤーを選択"""
    cursor.execute("SELECT user_id, name, level, gold, medal_count FROM quest_users")
    users = cursor.fetchall()

    if not users:
        print("ユーザーが見つかりません。")
        sys.exit()

    print("\n--- ① プレイヤー選択 ---")
    for idx, u in enumerate(users):
        # u: (user_id, name, level, gold, medal_count)
        print(f"{idx + 1}. {u[1]} (ID: {u[0]}) - Lv.{u[2]}")

    while True:
        try:
            choice = input("\n番号を入力してください: ")
            idx = int(choice) - 1
            if 0 <= idx < len(users):
                return users[idx]
        except ValueError:
            pass
        print("無効な番号です。もう一度入力してください。")

def select_target():
    """② 対象を選択"""
    targets = [
        {"key": "gold", "label": "所持金 (Gold)", "column": "gold", "unit": "G"},
        {"key": "exp", "label": "経験値 (Experience)", "column": "exp", "unit": ""},
        {"key": "medal", "label": "ちいさなメダル", "column": "medal_count", "unit": "枚"}
    ]

    print("\n--- ② 変更項目の選択 ---")
    for idx, t in enumerate(targets):
        print(f"{idx + 1}. {t['label']}")

    while True:
        try:
            choice = input("\n番号を入力してください: ")
            idx = int(choice) - 1
            if 0 <= idx < len(targets):
                return targets[idx]
        except ValueError:
            pass
        print("無効な番号です。")

def input_amount(target_label):
    """③ 増減値を入力"""
    print(f"\n--- ③ 増減値の入力 ({target_label}) ---")
    print("・増やす場合: 正の数 (例: 100)")
    print("・減らす場合: 負の数 (例: -100)")
    
    while True:
        try:
            val = input("値を入力してください: ")
            return int(val)
        except ValueError:
            print("整数を入力してください。")

def main():
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 1. プレイヤー選択
        # user = (user_id, name, level, gold, medal_count)
        user = select_player(cursor)
        user_id = user[0]
        user_name = user[1]
        
        # 最新の状態を再取得 (選択時とのズレ防止)
        cursor.execute("SELECT level, exp, gold, medal_count FROM quest_users WHERE user_id = ?", (user_id,))
        current_data = cursor.fetchone()
        current_level, current_exp, current_gold, current_medals = current_data

        # 2. 対象選択
        target = select_target()

        # 3. 増減値入力
        amount = input_amount(target['label'])

        # 計算ロジック
        new_level = current_level
        new_exp = current_exp
        new_gold = current_gold
        new_medals = current_medals
        
        change_desc = ""

        if target['key'] == 'gold':
            new_gold = max(0, current_gold + amount)
            change_desc = f"{current_gold}G → {new_gold}G ({'+' if amount > 0 else ''}{amount}G)"
        
        elif target['key'] == 'medal':
            new_medals = max(0, current_medals + amount)
            change_desc = f"{current_medals}枚 → {new_medals}枚 ({'+' if amount > 0 else ''}{amount}枚)"
            
        elif target['key'] == 'exp':
            # 経験値の場合はレベル計算も行う
            if amount >= 0:
                new_level, new_exp, _ = GameLogic.calc_level_progress(current_level, current_exp, amount)
            else:
                new_level, new_exp = GameLogic.calc_level_down(current_level, current_exp, abs(amount))
            
            change_desc = f"Lv.{current_level} (Exp.{current_exp}) → Lv.{new_level} (Exp.{new_exp})"
            if new_level != current_level:
                change_desc += f"\n   ※レベルが {new_level - current_level:+d} 変わります！"

        # 4. 確認
        print("\n" + "="*40)
        print("   ④ 変更内容の確認")
        print("="*40)
        print(f"プレイヤー : {user_name} (ID: {user_id})")
        print(f"対象項目   : {target['label']}")
        print(f"変更内容   : {change_desc}")
        print("="*40)

        confirm = input("この内容でDBを更新してよろしいですか？ (y/n): ").lower()

        # 5. 反映
        if confirm == 'y':
            now = datetime.datetime.now().isoformat()
            
            if target['key'] == 'gold':
                cursor.execute("UPDATE quest_users SET gold=?, updated_at=? WHERE user_id=?", 
                               (new_gold, now, user_id))
            elif target['key'] == 'medal':
                cursor.execute("UPDATE quest_users SET medal_count=?, updated_at=? WHERE user_id=?", 
                               (new_medals, now, user_id))
            elif target['key'] == 'exp':
                cursor.execute("UPDATE quest_users SET level=?, exp=?, updated_at=? WHERE user_id=?", 
                               (new_level, new_exp, now, user_id))
            
            conn.commit()
            print("\n✅ ⑤ DBへの反映が完了しました！")
        else:
            print("\n❌ キャンセルしました。変更は行われませんでした。")

    except Exception as e:
        print(f"\n❌ エラーが発生しました: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()