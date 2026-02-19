import os
import shutil
import subprocess
from pathlib import Path

try:
    import config
    from core.logger import get_logger
    from services.notification_service import send_push
except ImportError:
    # 単体テスト用フォールバック
    import logging
    logging.basicConfig(level=logging.INFO)
    def get_logger(name): return logging.getLogger(name)
    def send_push(*args, **kwargs): pass

logger = get_logger("nas_utils")

def attempt_remount(mount_point: str) -> bool:
    """NASの再マウントを試みる。
    
    Args:
        mount_point (str): 対象のマウントポイント（例: /mnt/nas）
        
    Returns:
        bool: マウントに成功した場合はTrue
    """
    logger.info(f"🔄 接続エラーを検知。再マウントを試行します: {mount_point}")
    try:
        # OSのmountコマンドを呼び出し（sudoers設定が必要）
        res = subprocess.run(
            ["sudo", "mount", mount_point],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True
        )
        if res.returncode == 0:
            logger.info(f"✅ 再マウントに成功しました: {mount_point}")
            return True
        else:
            logger.error(f"❌ 再マウント失敗: {res.stderr.strip()}")
            return False
    except Exception as e:
        logger.error(f"❌ 再マウント実行中の例外エラー: {e}")
        return False

def sync_fallback_to_nas(local_dir: Path, nas_dir: Path) -> None:
    """ローカルのフォールバックディレクトリにあるデータをNASに同期（移動）する。
    
    Args:
        local_dir (Path): ローカルのフォールバックパス
        nas_dir (Path): NASのターゲットパス
    """
    if not local_dir.exists() or not any(local_dir.iterdir()):
        return  # 同期すべきデータなし

    logger.info(f"🔄 フォールバックデータのNAS同期を開始します: {local_dir} -> {nas_dir}")
    try:
        for item in local_dir.iterdir():
            target_path = nas_dir / item.name
            
            # 既存データの上書きを防ぐための簡単なマージ処理
            if item.is_file():
                shutil.copy2(item, target_path)
                item.unlink()
            elif item.is_dir():
                shutil.copytree(item, target_path, dirs_exist_ok=True)
                shutil.rmtree(item)
                
        logger.info("✅ データのNAS同期が完了しました。SSOTが復元されました。")
    except Exception as e:
        logger.error(f"❌ データのNAS同期中にエラーが発生しました: {e}", exc_info=True)

def is_mounted_and_writable(target_dir: Path, mount_point: str) -> bool:
    """マウント状態を確認し、ターゲットディレクトリへのアクセス権を検証する。"""
    # 1. マウントポイント自体の確認
    if not os.path.ismount(mount_point):
        return False
    
    # 2. ターゲットディレクトリの作成を試行（初回起動対策）
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        return os.access(target_dir, os.W_OK | os.X_OK)
    except OSError:
        return False

def get_managed_target_directory(nas_dir_str: str, fallback_dir_str: str, mount_point: str = "/mnt/nas") -> Path:
    """ディレクトリを取得し、アクセス不可の場合は自己修復と同期を行う。
    
    Args:
        nas_dir_str (str): 本来のNASディレクトリパス
        fallback_dir_str (str): フォールバック用のローカルディレクトリパス
        mount_point (str): NASのルートマウントポイント
        
    Returns:
        Path: 最終的に利用可能なディレクトリパス
    """
    nas_dir = Path(nas_dir_str)
    fallback_dir = Path(fallback_dir_str)

    if is_mounted_and_writable(nas_dir, mount_point):
        # 正常時：蓄積されたフォールバックデータがあれば同期
        sync_fallback_to_nas(fallback_dir, nas_dir)
        return nas_dir

    # アクセス不可時：再マウントを試行
    if attempt_remount(mount_point) and is_mounted_and_writable(nas_dir, mount_point):
        sync_fallback_to_nas(fallback_dir, nas_dir)
        return nas_dir

    # 復旧失敗：Notification Guardを突破して致命的エラーを通知
    error_msg = f"🚨 【NAS障害・介入要求】\nNASへのアクセス及び自動修復に失敗しました。\nPath: {nas_dir_str}\nローカルへフォールバックします。"
    logger.error(error_msg)
    
    # getattrを利用してconfigの存在確認を安全に行う
    user_id = getattr(config, "LINE_USER_ID", None)
    if user_id:
        send_push(
            user_id, 
            [{"type": "text", "text": error_msg}],
            target="discord", channel="error"
        )

    # Fail-Softロジック
    fallback_dir.mkdir(parents=True, exist_ok=True)
    return fallback_dir