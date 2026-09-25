# MY_HOME_SYSTEM/services/system_maintenance_service.py
"""システムページ(かんたん表示)の「サービス再起動」操作。

#829: 以前はStreamlit版ダッシュボード(`views/dashboard/log_tab.py`)の中に直接
書かれていたが、Streamlit版廃止に伴いシステムページ(`routers/dashboard_router.py`)の
POSTエンドポイントから呼べるサービス関数として独立させた。ロジック自体
(sudo systemctl restart, timeout付き)は変更していない。
"""
import subprocess

from core.logger import setup_logging

logger = setup_logging("system_maintenance")

# #651: systemctl 等の外部コマンドが応答しない場合にサーバーを固めないための上限(秒)。
RESTART_TIMEOUT_SEC: int = 30


def restart_home_system() -> tuple[bool, str]:
    """`home_system` サービスを再起動する。戻り値は (成功したか, メッセージ)。"""
    try:
        subprocess.run(
            ["sudo", "systemctl", "restart", "home_system"],
            check=True,
            timeout=RESTART_TIMEOUT_SEC,
        )
        return True, "再起動コマンドを送信しました"
    except subprocess.TimeoutExpired:
        logger.error(f"サービス再起動が {RESTART_TIMEOUT_SEC} 秒以内に完了しませんでした")
        return False, (
            f"再起動コマンドが {RESTART_TIMEOUT_SEC} 秒以内に完了しませんでした。"
            "systemctl 側の状態を確認してください。"
        )
    except Exception as e:
        logger.error(f"サービス再起動に失敗しました: {e}")
        return False, f"エラー: {e}"
