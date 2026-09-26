import contextlib
import re
import sqlite3
import os
import sys
import datetime
import shutil
import subprocess
from pathlib import Path
from typing import Tuple
# 設計書 (Source: 137) に従い core.logger を使用
from core.logger import setup_logging  # 設計書に従い core.logger を使用 [cite: 137, 354]
from services.notification_service import send_push
import config

# ロガー設定
logger = setup_logging("backup")

def perform_backup() -> Tuple[bool, str, float]:
    """
    データベースのバックアップを実行し、NASへ転送する。 [cite: 316]
    
    NASへの転送失敗（権限エラー・接続断等）は、管理者の介入が必要な恒久的障害（ERROR）として扱い、
    即時通知を行う。 [cite: 387, 469, 470]

    Returns:
        Tuple[bool, str, float]: (成功フラグ, メッセージ, バックアップサイズMB)
    """
    src_db_path = config.SQLITE_DB_PATH
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"home_system_{timestamp}.db"
    
    # パス設定
    temp_dir = Path(config.BASE_DIR) / "temp_backups"
    temp_path = temp_dir / filename
    nas_root = getattr(config, "NAS_PROJECT_ROOT", os.path.join(config.NAS_MOUNT_POINT, "home_system"))
    nas_backup_dir = Path(nas_root) / "db_backups"
    nas_final_path = nas_backup_dir / filename

    logger.info("🚀 Starting Robust Backup Process")
    
    try:
        # Phase 1: Local Backup (Fast & Safe)
        os.makedirs(temp_dir, exist_ok=True)
        # #411 S-L8: `with sqlite3.connect(...) as conn:` はトランザクションの
        # commit/rollbackのみを行い、接続自体はcloseしない(sqlite3の既知の挙動)。
        # 定期実行されるバックアップ処理で接続がcloseされずに残り続けるのを防ぐため
        # contextlib.closingで明示的にcloseする。
        with contextlib.closing(sqlite3.connect(src_db_path)) as src_conn, \
             contextlib.closing(sqlite3.connect(str(temp_path))) as dst_conn:
            src_conn.backup(dst_conn, pages=-1)

        # Issue #753 (AUDIT-024): backup() API は正常完了すれば一貫したコピーになるが、
        # コピー元が既に破損していれば破損したままコピーされる。NAS転送後の検証
        # (Phase 2のサイズ比較)も内容は見ていないため、破損に気づかないまま
        # DB_BACKUP_RETENTION_DAYS(既定30日)で健全な世代が消えうる。
        # PRAGMA integrity_check はDB全体を読むため時間がかかる(数百MBで数秒〜数十秒)が、
        # 1日1回04:00の実行なので許容する。
        with contextlib.closing(sqlite3.connect(str(temp_path))) as verify_conn:
            result = verify_conn.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise OSError(f"バックアップの整合性検証に失敗しました: {result}")

        local_size_mb = os.path.getsize(temp_path) / (1024 * 1024)
        logger.info(f"✅ Local backup created & verified: {local_size_mb:.2f} MB")

        # Phase 2: Transfer to NAS
        if not nas_backup_dir.exists():
            try:
                os.makedirs(nas_backup_dir, exist_ok=True)
            except (PermissionError, OSError) as e:
                # ここで通知すると、下の外側except節でも再度通知され二重送信になるため、
                # ログのみ残してメッセージ付きで再送出し、通知は外側の1箇所に一本化する。
                logger.error(f"❌ NASディレクトリ作成失敗: {e}")
                raise OSError(f"NASディレクトリ作成失敗: {e}") from e

        shutil.copy2(temp_path, nas_final_path)

        # 転送確認
        if nas_final_path.exists() and os.path.getsize(nas_final_path) == os.path.getsize(temp_path):
            os.remove(temp_path)
            logger.info(f"✅ Backup successfully transferred to NAS: {nas_final_path}")
            _backup_config_files(nas_backup_dir, timestamp, src_db_path)
            _copy_latest_offsite(nas_final_path)
            return True, "バックアップ完了", local_size_mb
        else:
            raise OSError("NAS転送後の整合性確認に失敗しました。")

    except Exception as e:
        error_msg = f"バックアッププロセス異常終了: {str(e)}"
        _notify_and_log_error(error_msg)
        if temp_path.exists():
            os.remove(temp_path)
        # #248: shutil.copy2()がNAS側の容量不足・切断等でコピー途中に失敗した場合、
        # または転送後の整合性確認(サイズ比較)に失敗した場合、NAS側には書きかけ・
        # 破損した不完全なファイル(nas_final_path)がそのまま残置されていた。
        # ローカルの一時ファイルと同様に、NAS側の不完全なファイルも削除を試みる。
        # 削除自体の失敗(NASが切断されている等)でこの例外処理全体が中断しない
        # よう、個別にtry-exceptで保護する。
        if nas_final_path.exists():
            try:
                os.remove(nas_final_path)
            except OSError as cleanup_err:
                logger.error(f"❌ NAS側の不完全なバックアップファイルの削除に失敗: {cleanup_err}")
        return False, str(e), 0.0

# === 設定ファイルのバックアップ時の伏せ字処理 (Issue #829) ===
# `devices.json` はカメラの `user` / `pass` を平文で持ち、`rtsp_url` にも
# `rtsp://<user>:<pass>@...` の形で認証情報を埋め込んでいる。以前はこれを毎晩そのまま
# NAS(CIFS の file_mode=0664)へコピーしており、2026-09-21 時点で `devices_*.json` 22件
# すべてにカメラのパスワードが入っていた。
#
# ホスト設定バックアップ(#774, services/host_config_backup_service.py)と同じく
# 「秘密の値は保全せず、構造だけを残す」方針にする。ユーザー名は残す(それ自体は秘密ではなく、
# 復元時にどのアカウントを使えばよいかが分かる)。
REDACTED_MARK = "***REDACTED-BY-backup_service***"

# JSON の文字列値のうち、キー名が認証情報を表すもの。値だけを置き換える。
# JSON として読み直さず正規表現で置き換えるのは、元ファイルが壊れていても(=読めなくても)
# 秘密を素通しでコピーしないため。書式(インデント・キー順)も保たれる。
_JSON_SECRET_FIELD_RE = re.compile(
    r'("(?:pass|passwd|password|passphrase|secret|token|api[_-]?key)"\s*:\s*")'
    r'((?:[^"\\]|\\.)+)'
    r'(")',
    re.IGNORECASE,
)
# URL に埋め込まれたパスワード(scheme://user:<ここ>@host)。ユーザー名は残す。
_URL_PASSWORD_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://[^:/@\s\"']+:)([^@\s\"'/]+)(@)")


def redact_backup_text(text: str, *, is_json: bool) -> tuple[str, int]:
    """バックアップ用に認証情報の値を伏せ字にした本文と、置き換えた件数を返す。

    URL に埋め込まれたパスワードはどのファイルでも落とす。JSON のキー名に基づく置き換えは
    `.json` だけに適用する(Python ソース等に同じ見た目の文字列があっても意味が違うため)。
    """
    count = 0

    def _sub(m: re.Match) -> str:
        nonlocal count
        count += 1
        return f"{m.group(1)}{REDACTED_MARK}{m.group(3)}"

    text = _URL_PASSWORD_RE.sub(_sub, text)
    if is_json:
        text = _JSON_SECRET_FIELD_RE.sub(_sub, text)
    return text, count


def _backup_config_files(nas_backup_dir: Path, timestamp: str, src_db_path: str) -> None:
    """config.BACKUP_FILES に列挙された設定ファイル(DB以外)をNASへコピーする。

    DBエントリ(src_db_path)は上のPhase 1/2で既にバックアップ済みのためスキップする。
    個々のファイルのコピー失敗はDBバックアップ自体の成否には影響させず、ログのみ残す。

    Issue #829: 認証情報の値は `redact_backup_text` で伏せ字にしてから書き出す。
    テキストとして読めないファイルは、秘密を素通しにしないよう**コピーしない**。
    """
    for entry in getattr(config, "BACKUP_FILES", []):
        if entry == src_db_path:
            continue
        src_path = entry if os.path.isabs(entry) else os.path.join(config.BASE_DIR, entry)
        if not os.path.exists(src_path):
            logger.warning(f"⚠️ バックアップ対象ファイルが見つかりません: {src_path}")
            continue
        src_path_obj = Path(src_path)
        dest_path = nas_backup_dir / f"{src_path_obj.stem}_{timestamp}{src_path_obj.suffix}"
        try:
            try:
                text = src_path_obj.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # 認証情報の有無を確かめられないものは、平文で NAS に置かない側へ倒す。
                logger.error(f"❌ テキストとして読めないためバックアップしません (秘密を検査できない): {src_path}")
                continue
            redacted, count = redact_backup_text(text, is_json=src_path_obj.suffix == ".json")
            dest_path.write_text(redacted, encoding="utf-8")
            shutil.copystat(src_path, dest_path)
            suffix = f" (認証情報 {count} 箇所を伏せ字化)" if count else ""
            logger.info(f"✅ 設定ファイルをバックアップしました: {src_path} -> {dest_path}{suffix}")
        except OSError as e:
            logger.error(f"❌ 設定ファイルのバックアップ失敗 ({src_path}): {e}")

# オフサイト複製(rclone)の上限時間。155MB 前後の DB を家庭用回線で送る想定で余裕を取る。
OFFSITE_TIMEOUT_SEC: int = 1800
OFFSITE_FILENAME: str = "home_system_latest.db"


def _copy_latest_offsite(nas_backup_path: Path) -> bool:
    """NAS へ転送済みのバックアップを、オフサイト(rclone のリモート)へ最新1世代として複製する。

    2026-09-19: 以前はバックアップが NAS にしか無く、NAS 故障時に DB 本体と
    バックアップを同時に失う構成だった。config.DB_BACKUP_OFFSITE_REMOTE が空なら何もしない。
    リモート側は常に "home_system_latest.db" の1ファイルだけを上書きする(世代管理は NAS 側)。

    失敗しても NAS へのバックアップ自体は成功しているため、perform_backup の戻り値には
    影響させない。ERROR ログを残し、health_watch(app_logs)の検知に任せる。
    """
    remote = getattr(config, "DB_BACKUP_OFFSITE_REMOTE", "")
    if not remote:
        return False
    rclone = shutil.which("rclone")
    if not rclone:
        logger.error("❌ オフサイト複製に失敗: rclone コマンドが見つかりません")
        return False
    dest = f"{remote.rstrip('/')}/{OFFSITE_FILENAME}"
    try:
        subprocess.run(  # nosec B603 - 引数はリスト渡しで、値は設定値とバックアップの実パスのみ
            [rclone, "copyto", str(nas_backup_path), dest, "--retries", "3"],
            check=True, capture_output=True, text=True, timeout=OFFSITE_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        logger.error(f"❌ オフサイト複製がタイムアウトしました({OFFSITE_TIMEOUT_SEC}秒): {dest}")
        return False
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or "").strip().splitlines()
        logger.error(f"❌ オフサイト複製に失敗 (rc={e.returncode}): {detail[-1] if detail else 'no stderr'}")
        return False
    logger.info(f"✅ 最新のバックアップをオフサイトへ複製しました: {dest}")
    return True


def _notify_and_log_error(message: str) -> None:
    """ERRORレベルの記録と管理者への即時通知を行う [cite: 361, 387]"""
    # Discord通知システム調査(2026-09-26)で、内容は障害通知なのにchannel="report"
    # (定期レポート用チャンネル)に送っていたことが判明したためerrorチャンネルへ変更。
    # 直後にsend_pushで同内容をerrorチャンネルへ送るため、logger.error自体の
    # DiscordErrorHandler経由の通知はskip_discordで抑止し、二重通知を避ける
    # (monitors/memory_monitor.pyと同じパターン)。
    logger.error(f"❌ {message}", extra={"skip_discord": True})
    send_push(
        messages=[{"type": "text", "text": f"🚨 【重要】バックアップ失敗報\n{message}"}],
        target="discord",
        channel="error"
    )

if __name__ == "__main__":
    # Issue #753 (AUDIT-024): 以前は戻り値を捨てていたため、バックアップが失敗しても
    # プロセスは exit 0 で終わっていた(通知はあるが、cron/systemd からは成功に見える)。
    # 終了コードで失敗が分かるようにする。
    sys.exit(0 if perform_backup()[0] else 1)