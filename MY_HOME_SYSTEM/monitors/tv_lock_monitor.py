# MY_HOME_SYSTEM/monitors/tv_lock_monitor.py
import sys
import os

# プロジェクトルートへのパス解決
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

import config
from core import state_file
from core.jp_holidays import is_offday
from core.logger import setup_logging
from core.utils import get_now_jst
from services import switchbot_service

logger = setup_logging("monitor.tv_lock")

# 重複実行を防ぐため、実行状態を記録するファイル(深夜2時のロック用)
LAST_RUN_FILE = os.path.join(config.FALLBACK_ROOT, "last_tv_lock.txt")
# 休日スロット用の記録ファイルは時刻ごとに分ける("last_tv_lock_1200.txt" 等)。
HOLIDAY_RUN_FILE_TEMPLATE = os.path.join(config.FALLBACK_ROOT, "last_tv_lock_{hhmm}.txt")

# (時, 操作, 休日のみか)。各スロットは「その時の0〜5分」に1日1回だけ実行する。
# 休日(土日・祝日・家の休み)は、12:00にオフ、14:00にオン(テレビを使える状態に戻す)、
# 20:00にオフ。12:00〜14:00と20:00以降の「オンできない」側は
# switchbot_service.is_tv_blocked_now がアプリ経由の自動ONを止めることで担保する。
_SLOTS = (
    (2, "turnOff", False),
    (12, "turnOff", True),
    (14, "turnOn", True),
    (20, "turnOff", True),
)


def _run_file_for(hour: int) -> str:
    if hour == 2:
        return LAST_RUN_FILE
    return HOLIDAY_RUN_FILE_TEMPLATE.format(hhmm=f"{hour:02d}00")


def main():
    if not config.TV_PLUG_DEVICE_ID:
        logger.debug("TV_PLUG_DEVICE_ID is not set. Skipping.")
        return

    # Issue #592: ホストOSのタイムゾーン設定に依存しないよう、naiveなdatetime.now()
    # ではなく明示的にJSTの現在時刻を使う(この判定はJSTの時刻を意図しており、
    # ホストがJST以外の設定だと別の実時刻に実行されてしまう)。
    now = get_now_jst()

    slot_executed = False
    for hour, command, holiday_only in _SLOTS:
        if now.hour != hour or not 0 <= now.minute <= 5:
            continue
        if holiday_only and not is_offday(now):
            continue

        today_str = now.strftime("%Y-%m-%d")
        run_file = _run_file_for(hour)

        # すでに本日実行済みかチェック
        # #661: 読み書きは core/state_file.py(tmp+fsync+os.replace)へ一本化した。
        if state_file.read_text(run_file) == today_str:
            continue  # すでに実行済み

        logger.info(f"📺 [TV Lock] Executing scheduled TV plug command {command} at {hour:02d}:00.")
        slot_executed = True
        try:
            if command == "turnOff":
                # テレビがついていれば、先にリモコンで消してからプラグを切る
                res = switchbot_service.turn_off_tv_gracefully()
            else:
                res = switchbot_service.send_device_command(config.TV_PLUG_DEVICE_ID, command)
            if res and res.get("statusCode") == 100:
                logger.info(f"✅ [TV Lock] Successfully sent {command} to TV plug.")

                # 実行完了の記録を保存
                state_file.write_text_atomic(run_file, today_str)
            else:
                logger.error(f"❌ [TV Lock] API Error: {res}")
        except Exception as e:
            logger.error(f"❌ [TV Lock] Exception during {command}: {e}")

    if not slot_executed:
        _cut_if_turned_on_while_blocked(now)


def _cut_if_turned_on_while_blocked(now) -> None:
    """休日の禁止時間帯に、プラグ本体のボタン等で入れ直されていたら再度切る(Issue #900)。

    5分間隔の起動ごとに消費電力を確認し、TV_POWER_ON_THRESHOLD_WATTS 以上なら
    リモコン経由で消してからプラグを切る。電力を取得できないときは切らない
    (誤って切るより見逃す方を選ぶ)。通知は送らない(家族との合意事項)。
    """
    if not switchbot_service.is_tv_blocked_now(now):
        return
    watts = switchbot_service.get_tv_power_watts()
    if watts is None or watts < config.TV_POWER_ON_THRESHOLD_WATTS:
        return
    logger.info(f"📺 [TV Lock] TV is on during the blocked period ({watts:.1f}W). Cutting it again.")
    res = switchbot_service.turn_off_tv_gracefully()
    if not res or res.get("statusCode") != 100:
        logger.error(f"❌ [TV Lock] Re-cut API Error: {res}")


if __name__ == "__main__":
    main()
