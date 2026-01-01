# MY_HOME_SYSTEM/sound_manager.py
import os
import subprocess
import logging
import shutil
import config
import common  # 追加

# 共通のログ設定を使用 (これで logs/server.log に出るようになります)
logger = common.setup_logging("sound")

def play(event_key: str):
    """
    指定されたイベントに対応する音声ファイルを再生する
    """
    filename = config.SOUND_MAP.get(event_key)
    if not filename:
        logger.warning(f"⚠️ Event key '{event_key}' not found in SOUND_MAP")
        return

    # 絶対パスに変換して確認（パス間違い防止）
    filepath = os.path.join(config.SOUND_DIR, filename)
    abs_path = os.path.abspath(filepath)
    
    if not os.path.exists(abs_path):
        # 以前はdebugでしたが、原因特定のためwarningに格上げします
        logger.warning(f"🔇 Sound file missing: {abs_path} (Event: {event_key})")
        return

    # プレイヤーコマンドの確認
    if not shutil.which(config.SOUND_PLAYER_CMD):
        logger.warning(f"⚠️ Player command '{config.SOUND_PLAYER_CMD}' not found.")
        return

    try:
        # コマンドの組み立て
        cmd = [config.SOUND_PLAYER_CMD]
        
        # オプション設定があれば追加
        if hasattr(config, "SOUND_PLAYER_ARGS") and config.SOUND_PLAYER_ARGS:
            cmd.extend(config.SOUND_PLAYER_ARGS)
            
        cmd.append(abs_path)

        # 実行ログ
        logger.info(f"🔊 Playing: {event_key} -> {abs_path} (Cmd: {cmd})")

        # 実行 (Fire and Forget)
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        logger.error(f"❌ Sound playback failed: {e}")