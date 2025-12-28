import sqlite3
import config
import os

def diagnose():
    print("🕵️‍♀️ 根本原因調査を開始します...")
    
    # 1. パスの確認
    db_path = config.SQLITE_DB_PATH
    print(f"📁 参照しているDBパス: {db_path}")
    if not os.path.exists(db_path):
        print("❌ DBファイルが存在しません！パス設定を確認してください。")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        # 2. テーブル定義（スキーマ）の確認
        print("\n🔍 'quest_users' テーブルの構造を確認中...")
        try:
            cur.execute("PRAGMA table_info(quest_users)")
            columns_info = cur.fetchall()
            
            if not columns_info:
                print("❌ 'quest_users' テーブルが存在しません！")
                return

            print(f"   カラム数: {len(columns_info)}")
            column_names = []
            for col in columns_info:
                # cid, name, type, notnull, dflt_value, pk
                print(f"   - {col[1]} ({col[2]})")
                column_names.append(col[1])
                
        except Exception as e:
            print(f"❌ テーブル情報取得エラー: {e}")
            return

        # 3. 現在のデータ確認
        print("\n🔍 現在登録されているデータ:")
        rows = cur.execute("SELECT * FROM quest_users").fetchall()
        existing_ids = []
        for row in rows:
            # 辞書化して表示
            r_dict = dict(row)
            print(f"   - {r_dict}")
            # user_id的なものを探して保存
            if 'user_id' in r_dict: existing_ids.append(r_dict['user_id'])
            elif 'id' in r_dict: existing_ids.append(r_dict['id'])

        # 4. 「はるな」挿入テスト（INSERT OR IGNORE を使わずにエラーを見る）
        if 'mom' in existing_ids:
            print("\n✅ 'mom' は既に存在します（でも画面に出ないなら、カラムの中身が変かも？）")
        else:
            print("\n🧪 'mom' の挿入テストを実行します（エラーがあれば表示）...")
            
            # quest_router.py で使われているSQLとデータを模倣
            # ここでエラーが出れば、ソースコード側のSQLとDB定義が食い違っている
            
            # パターンA: init_unified_db.py の定義に基づくインサート
            try:
                # 試しに更新日時(updated_at)なしで入れてみる（routerの記述依存）
                sql = "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold) VALUES (?, ?, ?, ?, ?, ?)"
                data = ('mom', 'はるな', '魔法使い', 1, 0, 150)
                
                print(f"   実行SQL: {sql}")
                print(f"   データ: {data}")
                
                cur.execute(sql, data)
                print("   ✅ 成功しました！ (原因不明: なぜrouterでは失敗した？)")
                conn.rollback() # テストなので戻す
                
            except sqlite3.OperationalError as e:
                print(f"   ❌ SQL実行エラー (OperationalError): {e}")
                print("   👉 解説: カラムの数が合っていないか、名前が間違っています。")
            except sqlite3.IntegrityError as e:
                print(f"   ❌ 制約違反エラー (IntegrityError): {e}")
            except Exception as e:
                print(f"   ❌ その他のエラー: {e}")

    finally:
        conn.close()
        print("\n調査終了")

if __name__ == "__main__":
    diagnose()