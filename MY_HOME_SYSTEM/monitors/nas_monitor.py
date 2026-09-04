import os
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from typing import Dict, Optional, Any, Tuple

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 自作モジュール
import config
from core.logger import setup_logging
from core.database import save_log_generic
from core.utils import get_now_iso, retry_with_backoff
from services.notification_service import send_push

# ロガー設定
logger = setup_logging("nas_monitor")

class NasMonitor:
    """NASの状態監視、ディスク使用量の確認、および障害復旧時の自動切り戻しを行うクラス"""
    
    def __init__(self) -> None:
        self.ip: str = getattr(config, "NAS_IP", "192.168.1.20")
        self.mount_point: str = getattr(config, "NAS_MOUNT_POINT", "/mnt/nas")
        # NAS_PROJECT_ROOT は mount_point 配下のアプリ専用ディレクトリ(home_system)。
        # ASSETS_DIR 等はNAS未マウント時にフォールバックパスへ動的に切り替わるため、
        # 同期先には(現在値に依存しない)固定のNAS_PROJECT_ROOTを使う。
        self.nas_project_root: str = getattr(
            config, "NAS_PROJECT_ROOT", os.path.join(self.mount_point, "home_system")
        )
        self.fallback_dir: str = getattr(config, "FALLBACK_ROOT", "/tmp/temp_fallback")
        self.timeout: int = getattr(config, "NAS_CHECK_TIMEOUT", 5)
        self.write_check_retries: int = getattr(config, "NAS_WRITE_CHECK_RETRIES", 3)
        self.device_name: str = "BUFFALO LS720D"
        # /tmp はコンテナ/プロセス再起動で消える環境があり、そこに状態を置くと
        # 再起動のたびにヘルス状態がデフォルト(正常)へリセットされてしまう
        # (実際は障害中でも「正常」とみなされ、真の復旧時にフォールバック同期が
        # 行われなくなる)。永続化のためBASE_DIR配下のdataディレクトリに置く。
        self.state_file: str = os.path.join(
            getattr(config, "BASE_DIR", os.path.dirname(os.path.abspath(__file__))),
            "data", "nas_monitor_state.json"
        )

    def _load_state(self) -> Dict[str, Any]:
        """前回の監視状態をファイルから読み込む"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"State load error: {e}")
        return {"is_healthy": True}  # デフォルトは正常とみなす

    def _save_state(self, state: Dict[str, Any]) -> None:
        """現在の監視状態をファイルへ保存する"""
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f)
        except Exception as e:
            logger.error(f"State save error: {e}")

    def check_ping(self) -> bool:
        """NASへのPing疎通確認"""
        try:
            cmd = ["ping", "-c", "1", "-W", str(self.timeout), self.ip]
            res = subprocess.run(
                cmd, 
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL
            )
            return res.returncode == 0
        except Exception as e:
            logger.error(f"Ping check error: {e}")
            return False

    def check_mount(self) -> bool:
        """マウントポイントが正しくマウントされているか確認"""
        if not os.path.exists(self.mount_point):
            return False
        return os.path.ismount(self.mount_point)

    def _write_test_filename(self) -> str:
        """書き込みテスト用のファイル名を毎回一意に生成する。
        固定名を使い回すと、タイムアウトでサブプロセスをkillした際にremove()まで
        到達できずファイルが残留し、CIFS側のオープンハンドルが不整合な状態になる。
        次回以降のチェックが同じファイル名に対してこの不整合の解消待ち(oplock解放待ち)
        で再び時間がかかり、またkillされて残留する…という自己永続的な失敗ループに
        陥ることが実機調査で判明したため、毎回異なる名前を使って回避する。"""
        return f".write_test.{os.getpid()}.{time.time_ns()}"

    def check_write_permission(self) -> bool:
        """NASへの実際の書き込み・削除が可能かテストする。
        CIFSマウントがストールしていると open()/write()/remove() がブロックしたまま
        戻らず監視プロセスごとハングする恐れがあるため、別プロセスでI/Oを実行し
        タイムアウト付きで待ち受ける(タイムアウト時はプロセスをkillして戻る)。

        タイムアウトは、autofsがアイドル中にアンマウントした直後の再トリガーや
        NAS本体のディスクスピンアップにより発生することがあり、これは一過性の
        遅延であって恒久障害とは限らない(ENOENTでリトライ後に成功する
        config_init側の遅延と同種の事象)。そのため単発のタイムアウトで
        即座に「異常」と判定せず、Exponential Backoffで再試行する。

        各試行では毎回異なるファイル名を使う(_write_test_filename())。固定名を
        使い回すと、タイムアウトでサブプロセスをkillした際にremove()まで到達
        できずファイルが残留し、CIFS側のオープンハンドルが不整合な状態になる。
        次の試行(リトライ内・次回実行時のいずれも)が同じファイル名に対して
        この不整合の解消待ち(oplock解放待ち)で再び時間がかかり、またkillされて
        残留する…という自己永続的な失敗ループに陥ることが実機調査で判明した。"""
        script = (
            "import os, sys\n"
            "path = sys.argv[1]\n"
            "with open(path, 'w') as f:\n"
            "    f.write('health_check')\n"
            "os.remove(path)\n"
        )

        def _attempt() -> None:
            test_file = os.path.join(self.mount_point, self._write_test_filename())
            subprocess.run(
                [sys.executable, "-c", script, test_file],
                timeout=self.timeout,
                check=True,
                capture_output=True,
            )

        def _on_retry(attempt: int, delay: float, e: BaseException) -> None:
            logger.warning(
                f"⚠️ [Attempt {attempt + 1}/{self.write_check_retries}] "
                f"Write permission check timed out after {self.timeout}s "
                f"(NAS mount possibly still waking up). Retrying in {delay:.0f}s..."
            )

        try:
            # write_check_retries は「総試行回数」を表す既存の意味を維持するため、
            # retry_with_backoffの max_retries(初回を含まない追加リトライ回数)には
            # -1 したものを渡す。Exponential Backoff (1s, 2s, 4s, ...) は上限なし
            # (config.py側と異なり、CIFSストール解消までどれだけでも待たせたいため)。
            retry_with_backoff(
                _attempt,
                max_retries=self.write_check_retries - 1,
                retryable_exceptions=(subprocess.TimeoutExpired,),
                base_delay=1.0,
                on_retry=_on_retry,
            )
            return True
        except subprocess.TimeoutExpired:
            # 「起床待ちで失敗したのか」「本当に無応答なのか」を切り分けられるよう、
            # 最終失敗時のみ軽量なping/mount確認を添えてログに残す
            # (ping/mountが両方OKなら書き込みI/Oだけが遅い=ディスク起床待ちの可能性が高く、
            # pingすら通らなければネットワーク/NAS本体側の障害を疑う材料になる)。
            diag_ping_ok = self.check_ping()
            diag_mount_ok = self.check_mount() if diag_ping_ok else False
            logger.error(
                f"Write permission check timed out after {self.timeout}s "
                f"x {self.write_check_retries} attempts (NAS mount possibly stalled) "
                f"[diagnostic: ping={diag_ping_ok}, mount={diag_mount_ok}]"
            )
            return False
        except (subprocess.CalledProcessError, OSError) as e:
            logger.error(f"Write permission check error: {e}")
            return False

    def sync_fallback_data(self) -> None:
        """フォールバックディレクトリの assets データをNASへ安全に同期・移動する。

        FALLBACK_ROOT直下には last_memory_alert.txt / last_tv_lock.txt など、
        他モニターがローカル専用の状態管理に使うファイルも同居している。
        同期対象は本来NAS(ASSETS_DIR)に属するデータである assets サブディレクトリに限定し、
        それら無関係な状態ファイルを巻き込んで移動・削除しないようにする。
        """
        fallback_assets_dir = os.path.join(self.fallback_dir, "assets")
        if not os.path.exists(fallback_assets_dir) or not os.listdir(fallback_assets_dir):
            logger.debug("フォールバックディレクトリに同期対象のデータはありません。")
            return

        nas_assets_dir = os.path.join(self.nas_project_root, "assets")
        logger.info(f"Starting fallback data sync from {fallback_assets_dir} to {nas_assets_dir}")

        # rsyncを使用して安全に転送。--remove-source-filesで転送完了したファイルのみ元から削除
        cmd = [
            "rsync", "-av", "--remove-source-files",
            f"{fallback_assets_dir}/",
            f"{nas_assets_dir}/"
        ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if res.returncode == 0:
                logger.info("✅ NAS restored and fallback data synced.")

                # 通知（復旧および同期完了）
                send_push(
                    [{"type": "text", "text": f"🟢 【NAS復旧】\nNASの復旧と、ローカルからのデータ同期が完了しました。\nPath: {nas_assets_dir}"}],
                    target="discord", channel="report"
                )

                # rsync --remove-source-files は空ディレクトリを残すため、クリーンアップ
                self._cleanup_empty_dirs(fallback_assets_dir)
            else:
                logger.error(f"Sync failed with rsync error: {res.stderr}")
        except subprocess.TimeoutExpired:
            logger.error("Sync process exception: rsync timed out after 120s (NAS mount unresponsive)")
        except Exception as e:
            logger.error(f"Sync process exception: {e}")

    def _cleanup_empty_dirs(self, path: str) -> None:
        """指定パス配下の空ディレクトリを再帰的に削除する"""
        for root, dirs, files in os.walk(path, topdown=False):
            for d in dirs:
                dir_path = os.path.join(root, d)
                try:
                    os.rmdir(dir_path)
                except OSError:
                    pass  # 中身があるディレクトリは無視

    def get_disk_usage(self) -> Optional[Dict[str, float]]:
        """ディスク使用量を取得 (GB単位)"""
        try:
            total, used, free = shutil.disk_usage(self.mount_point)
            return {
                "total_gb": round(total / (2**30), 2),
                "used_gb": round(used / (2**30), 2),
                "free_gb": round(free / (2**30), 2),
                "percent": round(used / total * 100, 1)
            }
        except Exception as e:
            logger.error(f"Disk usage check error: {e}")
            return None

    def cleanup_old_files(
        self, directory: str, retention_days: int, extensions: Optional[Tuple[str, ...]]
    ) -> Dict[str, Any]:
        """指定ディレクトリ配下を再帰的に走査し、保持日数を超えたファイルを削除する。

        extensions が None の場合は拡張子で絞り込まず、ディレクトリ内の全ファイルを対象とする
        (ディレクトリ自体がバックアップ専用など、単一種類の成果物しか置かれないことが
        保証されている場合に使う。Issue #191)。
        """
        result: Dict[str, Any] = {"deleted_count": 0, "freed_gb": 0.0}

        if not directory or not os.path.isdir(directory):
            return result

        cutoff = time.time() - (retention_days * 86400)
        freed_bytes = 0

        for root, _dirs, files in os.walk(directory):
            for name in files:
                if extensions is not None and not name.lower().endswith(extensions):
                    continue
                path = os.path.join(root, name)
                try:
                    if os.path.getmtime(path) < cutoff:
                        size = os.path.getsize(path)
                        os.remove(path)
                        result["deleted_count"] += 1
                        freed_bytes += size
                except OSError as e:
                    logger.warning(f"Cleanup skip (error): {path}: {e}")

        result["freed_gb"] = round(freed_bytes / (2**30), 2)
        return result

    def run_retention_cleanup(self) -> None:
        """保持期間を超えたNVR録画・カメラスナップショット・DBバックアップを削除する。"""
        targets = [
            ("NVR録画", getattr(config, "NVR_RECORD_DIR", None),
             getattr(config, "RECORDING_RETENTION_DAYS", 30), (".mp4",)),
            ("スナップショット", os.path.join(getattr(config, "ASSETS_DIR", ""), "snapshots"),
             getattr(config, "RECORDING_RETENTION_DAYS", 30), (".jpg", ".jpeg")),
            # タイムラプス動画の生成先(monitors/smart_timelapse_generator.pyの
            # setup_directories)はNAS(config.ASSETS_DIR)ではなくローカルの
            # config.BASE_DIR/assets/timelapse であり、以前はここがNAS側の
            # パスを指していたため、誰も書かないディレクトリを掃除し、誰も
            # 掃除しないローカルディレクトリにファイルが無限蓄積していた
            # (Issue #171)。生成先と同じローカルパスに修正する。
            ("タイムラプス動画", os.path.join(getattr(config, "BASE_DIR", ""), "assets", "timelapse"),
             getattr(config, "RECORDING_RETENTION_DAYS", 30), (".mp4", ".jpg")),
            # DB_BACKUPS_DIR は services/backup_service.py の Phase 2 (DBダンプ)と
            # _backup_config_files (config.BACKUP_FILES 中のDB以外のファイル、例:
            # config.py/.env/devices.json) の両方の出力専用ディレクトリであり、
            # 拡張子は .db に限らない (.env はコピー時に拡張子なしのファイル名になる)。
            # 以前は .db のみを削除対象としていたため、設定ファイルのバックアップ
            # コピーは一切削除されず無限に蓄積していた(Issue #191)。このディレクトリは
            # バックアップ専用でバックアップ以外のファイルが置かれることはないため、
            # 拡張子で絞り込まず全ファイルを対象にする。
            ("DBバックアップ", getattr(config, "DB_BACKUPS_DIR", None),
             getattr(config, "DB_BACKUP_RETENTION_DAYS", 30), None),
            # #359: 録画VOD用のHLSセグメント(services/camera_service.py の
            # generate_record_playlist が BASE_DIR/data/hls_streams/vod/<cam>/ に生成する
            # .ts/.m3u8/concat_*.txt)は1日分で数GB規模だが、どこにも削除経路が無く
            # ローカル(SDカード)に無制限に蓄積していた。過去日の閲覧結果はキャッシュとして
            # 数日残せば十分なので短い保持期間で削除する。
            ("録画VODキャッシュ",
             os.path.join(getattr(config, "BASE_DIR", ""), "data", "hls_streams", "vod"),
             getattr(config, "HLS_VOD_RETENTION_DAYS", 3), (".ts", ".m3u8", ".txt")),
        ]

        summary_lines = []
        for label, directory, retention_days, extensions in targets:
            if not directory:
                continue
            result = self.cleanup_old_files(directory, retention_days, extensions)
            if result["deleted_count"] > 0:
                logger.info(
                    f"🗑️ Cleanup {label} ({directory}): "
                    f"removed {result['deleted_count']} files, freed {result['freed_gb']} GB"
                )
                summary_lines.append(f"- {label}: {result['deleted_count']}件 / {result['freed_gb']}GB")

        if summary_lines:
            send_push(
                [{"type": "text", "text": "🗑️ **古いファイルの自動削除**\n" + "\n".join(summary_lines)}],
                target="discord", channel="report"
            )

    def save_to_db(self, ping_ok: bool, mount_ok: bool, usage: Optional[Dict[str, float]]) -> None:
        """状態をDBに保存"""
        percent = usage['percent'] if usage else 0
        save_log_generic(
            config.SQLITE_TABLE_SENSOR,
            ["timestamp", "device_name", "device_id", "device_type", "contact_state", "nas_usage_percent"],
            (
                get_now_iso(),
                "NAS_Monitor",
                self.ip,
                "Server",
                "mounted" if mount_ok else "unmounted",
                percent
            )
        )

        # ダッシュボードのNASステータスカード(views/dashboard/summary.py)・
        # NAS状態パネル(views/dashboard/log_tab.py)は device_records ではなく
        # config.SQLITE_TABLE_NAS(=nas_records)を読むが、以前はこのテーブルへ
        # INSERTする本番コードが存在せず、常に「データなし」表示のままだった
        # (Issue #168)。nas_records のスキーマ(status_ping/status_mount は
        # 'OK'/'NG' の文字列)に合わせて書き込む。
        save_log_generic(
            getattr(config, "SQLITE_TABLE_NAS", "nas_records"),
            ["timestamp", "device_name", "ip_address", "status_ping", "status_mount",
             "total_gb", "used_gb", "free_gb", "percent"],
            (
                get_now_iso(),
                self.device_name,
                self.ip,
                "OK" if ping_ok else "NG",
                "OK" if mount_ok else "NG",
                usage['total_gb'] if usage else None,
                usage['used_gb'] if usage else None,
                usage['free_gb'] if usage else None,
                percent
            )
        )

    def run(self) -> None:
        """NASの状態監視、復旧検知、およびディスク使用量の確認を実行する。"""
        
        ping_ok = self.check_ping()
        mount_ok = self.check_mount() if ping_ok else False
        write_ok = self.check_write_permission() if mount_ok else False
        
        is_currently_healthy = ping_ok and mount_ok and write_ok
        previous_state = self._load_state()
        was_healthy = previous_state.get("is_healthy", True)

        # 1. 状態遷移の検知（正常 -> 異常：フォールバック移行時）
        if not is_currently_healthy and was_healthy:
            logger.error(f"❌ NAS connection lost or write failed. Falling back to local storage. (Ping: {ping_ok}, Mount: {mount_ok}, Write: {write_ok})")
            send_push(
                [{"type": "text", "text": f"🚨 【NAS障害】\nNASへのアクセスが失われました。\nローカルフォールバックへ移行します。\nIP: {self.ip}"}],
                target="discord", channel="error"
            )
            previous_state["is_healthy"] = False
            self._save_state(previous_state)

        # 2. 状態遷移の検知（異常 -> 正常：NAS復旧時）
        elif is_currently_healthy and not was_healthy:
            logger.debug("NAS recovery detected. Initiating fallback data sync...")
            self.sync_fallback_data()
            previous_state["is_healthy"] = True
            self._save_state(previous_state)

        # DB記録
        usage = self.get_disk_usage() if is_currently_healthy else None
        self.save_to_db(ping_ok, mount_ok, usage)

        # 異常継続中の場合はここで処理終了（ログ汚染を防ぐ）
        if not is_currently_healthy:
            return

        # 3. 正常継続時の定常チェック
        logger.debug("NAS mount and write permissions are normal.")

        if not usage:
            return

        # 通知判定 (容量不足または定期レポート)
        is_full = usage['percent'] > 90
        now = datetime.now()
        is_report_time = (now.hour == 8)

        # 保持期間を超えた録画・バックアップの自動削除(1日1回)。
        # #388: 以前は「実行時刻の hour == 8」だけで判定していたが、scheduler の実行間隔は
        # 毎回 3600〜3610s と少しずつ後ろにずれるため、7:59 台の次が 9:00 台になる日は
        # 8時台の実行が無く、その日の削除がまるごとスキップされていた。状態ファイルに
        # 最終実行日を持ち、「今日まだ実行していない かつ 8時以降」で判定する。
        today_str = now.strftime("%Y-%m-%d")
        if now.hour >= 8 and previous_state.get("last_cleanup_date") != today_str:
            self.run_retention_cleanup()
            previous_state["last_cleanup_date"] = today_str
            self._save_state(previous_state)

        if not is_full and not is_report_time:
            return
        
        status_icon = "🔴" if is_full else "🟢"
        title = "容量不足警告" if is_full else "NAS稼働レポート"
        
        msg = (
            f"{status_icon} **{title}**\n"
            f"デバイス: {self.device_name} ({self.ip})\n"
            f"状態: 正常\n\n"
            f"💾 **ディスク使用率: {usage['percent']:.1f}%**\n"
            f"使用: {usage['used_gb']} GB / 全体: {usage['total_gb']} GB\n"
            f"(残り: {usage['free_gb']} GB)"
        )
        
        channel = "error" if is_full else "report"
        send_push(
            [{"type": "text", "text": msg}],
            target="discord", channel=channel
        )

if __name__ == "__main__":
    monitor = NasMonitor()
    monitor.run()