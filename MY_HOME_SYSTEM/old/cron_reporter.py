import os
import subprocess
import logging
import requests
import re
from datetime import datetime
from dotenv import load_dotenv

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('CronReporter')

class CronReporter:
    """
    現在のCrontab設定を解析し、分かりやすい日本語レポートとして
    LINEおよびDiscordに送信するクラス
    """
    
    REQUEST_TIMEOUT = 10 # 秒

    def __init__(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self._load_environment()

    def _load_environment(self):
        """環境変数をロード"""
        dotenv_path = os.path.join(self.base_dir, '.env')
        load_dotenv(dotenv_path)
        
        # 通知先設定
        self.line_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
        self.discord_webhook = os.getenv("DISCORD_WEBHOOK_REPORT")

        # 少なくともどちらか一つは設定されているべき
        if not self.line_token and not self.discord_webhook:
            logger.warning("⚠️ 通知先(LINE_ACCESS_TOKEN または DISCORD_WEBHOOK_REPORT)が設定されていません。")

    def _get_crontab_raw(self) -> list:
        """crontab -l の結果を行リストで取得"""
        try:
            # シェルコマンド実行
            result = subprocess.run(['crontab', '-l'], capture_output=True, text=True, check=True)
            return result.stdout.strip().split('\n')
        except subprocess.CalledProcessError:
            # crontabがまだ設定されていない場合などはここに来る
            return []
        except Exception as e:
            logger.error(f"❌ crontab取得時に予期せぬエラー: {e}")
            self._send_error_log(f"crontabコマンド実行エラー: {e}")
            return []

    def _human_readable_time(self, min_str, hour_str, day, month, wday):
        """Cronの時間を自然な日本語に変換する"""
        try:
            # パターン1: 分の間隔実行 (例: */5)
            if "*/" in min_str:
                interval = min_str.split("/")[1]
                return f"⏱️ {interval}分ごと"
            
            # 時間の整形 (例: 8,20 -> 08:00, 20:00)
            hours = hour_str.split(',')
            formatted_times = []
            for h in hours:
                if h == '*': continue
                # 時と分を2桁埋め
                h_fmt = h.zfill(2)
                m_fmt = min_str.zfill(2)
                formatted_times.append(f"{h_fmt}:{m_fmt}")
            
            time_display = ", ".join(formatted_times)

            # パターン2: 毎日実行
            if day == '*' and month == '*' and wday == '*':
                return f"毎日 {time_display}"

            # パターン3: 曜日指定
            if wday != '*':
                w_map = {'0': '日', '1': '月', '2': '火', '3': '水', '4': '木', '5': '金', '6': '土', '7': '日'}
                wdays = wday.split(',')
                w_str_list = [w_map.get(w, w) for w in wdays]
                w_str = ",".join(w_str_list)
                return f"毎週{w_str}曜 {time_display}"
            
            # その他
            return f"指定日({month}月{day}日) {time_display}"
        except Exception:
            # 解析不能な複雑な設定はそのまま返す
            return f"{min_str} {hour_str} {day} {month} {wday}"

    def _analyze_jobs(self):
        """設定行を解析して構造化データを返す"""
        lines = self._get_crontab_raw()
        parsed_jobs = []
        
        for line in lines:
            line = line.strip()
            # コメント、空行、環境変数設定はスキップ
            if not line or line.startswith('#') or '=' in line.split()[0]:
                continue
            
            parts = line.split()
            if len(parts) < 6:
                continue

            min_str, hour_str, day, month, wday = parts[:5]
            command_full = " ".join(parts[5:])
            
            # 日本語翻訳
            schedule_text = self._human_readable_time(min_str, hour_str, day, month, wday)
            
            # スクリプト名抽出 (.pyファイルがあればそれを、なければコマンド先頭)
            script_name = "コマンド実行"
            match = re.search(r'([\w_]+\.py)', command_full)
            if match:
                script_name = match.group(1)
            
            parsed_jobs.append({
                "schedule": schedule_text,
                "script": script_name,
                "raw_cmd": command_full
            })
            
        return parsed_jobs

    def report(self):
        """レポート作成と送信のメイン処理"""
        logger.info("⚙️ システム稼働状況の確認を開始します...")
        
        try:
            jobs = self._analyze_jobs()
            today_str = datetime.now().strftime('%Y-%m-%d %H:%M')

            # --- メッセージ作成 ---
            # Discord向け（詳細版）
            discord_msg = f"⚙️ **システム稼働レポート ({today_str})**\n"
            discord_msg += "お家の裏方さんたちが、以下のスケジュールで待機しています。\n"
            discord_msg += "━━━━━━━━━━━━━━━━━━━\n"

            # LINE向け（シンプル版）
            line_msg = f"お疲れ様です🌿\n現在の自動システム稼働状況です({today_str})\n\n"

            if not jobs:
                no_task_msg = "📭 現在、設定されている自動タスクはありません。"
                discord_msg += no_task_msg + "\n"
                line_msg += no_task_msg
            else:
                for job in jobs:
                    icon = "🐍" if ".py" in job['script'] else "⚙️"
                    
                    # Discord用
                    discord_msg += f"**{icon} {job['script']}**\n"
                    discord_msg += f"└ ⏰ **{job['schedule']}**\n"
                    # コマンド省略表示
                    short_cmd = job['raw_cmd']
                    if len(short_cmd) > 50:
                        short_cmd = short_cmd[:25] + " ... " + short_cmd[-20:]
                    discord_msg += f"└ 💻 `{short_cmd}`\n\n"

                    # LINE用（簡潔に）
                    line_msg += f"{icon} {job['script']}\n   ⏰ {job['schedule']}\n"

            discord_msg += "━━━━━━━━━━━━━━━━━━━\n"
            discord_msg += "※ 異常があれば別途エラー通知が届きます。"
            
            line_msg += "\n今日も順調に動いています😊"

            # --- 送信処理 ---
            self._send_discord(discord_msg)
            # self._send_line(line_msg)
            
            logger.info("✅ レポート送信完了")

        except Exception as e:
            logger.error(f"❌ レポート作成中にエラー: {e}")
            self._send_error_log(f"レポート作成失敗: {e}")

    def _send_discord(self, message):
        """Discord送信"""
        if not self.discord_webhook: return
        
        try:
            res = requests.post(
                self.discord_webhook, 
                json={"content": message}, 
                timeout=self.REQUEST_TIMEOUT
            )
            if res.status_code not in [200, 204]:
                logger.error(f"❌ Discord送信失敗: {res.status_code} - {res.text}")
        except Exception as e:
            logger.error(f"❌ Discord通信エラー: {e}")

    def _send_line(self, message):
        """LINE送信"""
        if not self.line_token: return

        try:
            url = "https://notify-api.line.me/api/notify"
            headers = {"Authorization": f"Bearer {self.line_token}"}
            res = requests.post(
                url, 
                headers=headers, 
                data={"message": message},
                timeout=self.REQUEST_TIMEOUT
            )
            if res.status_code != 200:
                logger.error(f"❌ LINE送信失敗: {res.status_code}")
        except Exception as e:
            logger.error(f"❌ LINE通信エラー: {e}")

    def _send_error_log(self, error_message):
        """エラー発生時の緊急通知（Discordのみ）"""
        if not self.discord_webhook: return
        
        msg = f"🚨 **CronReporter エラー発生** 🚨\n```\n{error_message}\n```"
        try:
            requests.post(self.discord_webhook, json={"content": msg}, timeout=self.REQUEST_TIMEOUT)
        except Exception:
            pass # エラー送信のエラーは無視

if __name__ == "__main__":
    reporter = CronReporter()
    reporter.report()