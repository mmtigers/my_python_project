# MY_HOME_SYSTEM/sound_manager.py
import os
import subprocess
import shutil
import config

# 基本設計書に準拠し、coreモジュールからloggerをインポート
from core.logger import setup_logging

logger = setup_logging("sound_manager")

def play(event_key: str) -> None:
    """
    指定されたイベントに対応する音声ファイルを非同期で再生する。

    外部コマンド実行時の標準出力および標準エラー出力を完全に抑制し、
    コンソールへの直接出力を防ぐ。また、実行時エラーは捕捉して
    システムログに記録し、システム全体を停止させない（Fail-Soft）。

    Args:
        event_key (str): 再生する音声イベントを示すキー
    """
    filename = config.SOUND_MAP.get(event_key)
    if not filename:
        logger.warning(f"⚠️ Event key '{event_key}' not found in SOUND_MAP")
        return

    # 絶対パスに変換して確認
    filepath = os.path.join(config.SOUND_DIR, filename)
    abs_path = os.path.abspath(filepath)
    
    if not os.path.exists(abs_path):
        logger.warning(f"🔇 Sound file missing: {abs_path} (Event: {event_key})")
        return

    # プレイヤーコマンドの存在確認
    if not shutil.which(config.SOUND_PLAYER_CMD):
        logger.error(f"❌ Player command '{config.SOUND_PLAYER_CMD}' not found.")
        return

    try:
        # コマンドの組み立て
        cmd = [config.SOUND_PLAYER_CMD]
        if hasattr(config, "SOUND_PLAYER_ARGS") and config.SOUND_PLAYER_ARGS:
            cmd.extend(config.SOUND_PLAYER_ARGS)
        cmd.append(abs_path)

        # 実行ログ
        logger.info(f"🔊 Playing: {event_key} -> {abs_path} (Cmd: {cmd})")

        # 実行 (Fire and Forget)
        # stdout/stderr に DEVNULL を指定し、外部プロセスの出力を完全に遮断
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL
        )
    except OSError as e:
        # コマンドが見つからない、権限がない等のOSレベルのエラー
        logger.error(f"❌ OS error occurred during sound playback (Event: {event_key}): {e}")
    except Exception as e:
        # その他の予期せぬエラー（Fail-Soft）
        logger.error(f"❌ Unexpected error during sound playback (Event: {event_key}): {e}")


def check_and_restore_sounds() -> None:
    """
    必要な音声ファイルが存在するかチェックし、
    欠損している場合はデフォルトディレクトリからコピーして復旧する。
    """
    if not os.path.exists(config.SOUND_DIR):
        try:
            os.makedirs(config.SOUND_DIR, exist_ok=True)
            logger.info(f"📁 Created sound directory: {config.SOUND_DIR}")
        except Exception as e:
            logger.error(f"❌ Failed to create sound dir: {e}")
            return

    logger.info("🎵 Checking sound files integrity...")
    
    restored_count = 0
    missing_count = 0

    for key, filename in config.SOUND_MAP.items():
        target_path = os.path.join(config.SOUND_DIR, filename)
        
        if not os.path.exists(target_path):
            logger.warning(f"⚠️ Missing sound file: {filename}")
            
            source_path = os.path.join(config.DEFAULT_SOUND_SOURCE, filename)
            
            if os.path.exists(source_path):
                try:
                    shutil.copy2(source_path, target_path)
                    logger.info(f"  ↳ ✅ Restored from defaults: {filename}")
                    restored_count += 1
                except Exception as e:
                    logger.error(f"  ↳ ❌ Failed to restore {filename}: {e}")
                    missing_count += 1
            else:
                logger.error(f"  ↳ ❌ Default source not found: {source_path}")
                missing_count += 1
    
    if restored_count > 0:
        logger.info(f"🎉 Restored {restored_count} sound files.")
    
    if missing_count > 0:
        logger.warning(f"🚨 {missing_count} sound files are still missing!")
    else:
        logger.info("✅ All sound files are ready.")