import sys
import os
import time

# パス設定: MY_HOME_SYSTEMフォルダをモジュール検索パスに追加
current_dir = os.getcwd()
sys.path.append(os.path.join(current_dir, 'MY_HOME_SYSTEM'))

try:
    import config
    import sound_manager
except ImportError as e:
    print(f"Error importing modules: {e}")
    sys.exit(1)

def run_test():
    print("--- Sound System Diagnostic ---")
    
    # 1. 設定の確認
    print(f"[Config Check]")
    print(f"SOUND_DIR: {getattr(config, 'SOUND_DIR', 'Not Defined')}")
    print(f"PLAYER_CMD: {getattr(config, 'SOUND_PLAYER_CMD', 'Not Defined')}")
    
    if not hasattr(config, 'SOUND_MAP'):
        print("❌ config.SOUND_MAPが見つかりません。")
        return

    keys = list(config.SOUND_MAP.keys())
    print(f"Available Keys: {keys}")
    print("-" * 30)

    # 2. 再生テスト
    if not keys:
        print("⚠️ 再生できるイベントキーが登録されていません。")
        return

    print("🔊 Starting playback test (3 seconds interval)...")
    for key in keys:
        filename = config.SOUND_MAP[key]
        print(f"▶️ Testing: '{key}' (File: {filename})")
        
        # ファイル存在チェック
        full_path = os.path.join(config.SOUND_DIR, filename)
        if os.path.exists(full_path):
            print(f"   File exists: OK")
            sound_manager.play(key)
        else:
            print(f"   ❌ File missing: {full_path}")
        
        time.sleep(3)

    print("-" * 30)
    print("✅ Test Sequence Finished.")

if __name__ == "__main__":
    run_test()