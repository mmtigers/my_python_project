# verify_ai_report.py
import subprocess
import sys
import os

# パス設定
base_dir = os.path.dirname(os.path.abspath(__file__))
# 修正箇所: 余計な 'MY_HOME_SYSTEM' 階層を削除
target_script = os.path.join(base_dir, "send_ai_report.py")

def run_test(target_arg):
    print(f"\n🧪 [Test] ターゲット: {target_arg} で実行テスト中...")
    
    # ファイル存在チェック
    if not os.path.exists(target_script):
        print(f"❌ ファイルが見つかりません: {target_script}")
        return

    cmd = [sys.executable, target_script, "--target", target_arg]
    
    try:
        # 実行して出力をリアルタイム表示
        result = subprocess.run(cmd, check=True)
        if result.returncode == 0:
            print(f"✅ テスト成功 ({target_arg})")
        else:
            print(f"❌ テスト失敗 ({target_arg}) Code: {result.returncode}")
    except subprocess.CalledProcessError as e:
        print(f"❌ 実行エラー: {e}")
    except Exception as e:
        print(f"❌ 予期せぬエラー: {e}")

if __name__ == "__main__":
    print(f"📂 作業ディレクトリ: {base_dir}")
    print(f"📄 対象スクリプト: {target_script}")

    if not os.path.exists(target_script):
        print(f"❌ エラー: 対象ファイルが見つかりません。")
        print("   verify_ai_report.py と send_ai_report.py は同じフォルダに置いてください。")
        sys.exit(1)

    # Discordへの送信テスト
    run_test("discord")