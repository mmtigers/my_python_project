# MY_HOME_SYSTEM/monitors/tv_lock_monitor.py
import datetime
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
# スロットでテレビのプラグを切った時刻(JSTの壁時計・tzなしisoformat)。再カットの猶予判定に使う。
LAST_CUT_FILE = os.path.join(config.FALLBACK_ROOT, "last_tv_cut_at.txt")
# スロットで切った直後は、プラグの消費電力が遅れて古い値のまま残り「まだオン」と誤判定して
# 2回目のリモコン信号(トグル)を送ってしまうため、この秒数は禁止時間帯の再カットをしない。
RECUT_GRACE_SECONDS = 300
# 休日スロット用の記録ファイルは時刻ごとに分ける("last_tv_lock_1200.txt" 等)。
HOLIDAY_RUN_FILE_TEMPLATE = os.path.join(config.FALLBACK_ROOT, "last_tv_lock_{hhmm}.txt")

# (時, 分, 操作, 休日のみか)。各スロットは「その時刻から5分以内」に1日1回だけ実行する。
# 休日(土日・祝日・家の休み)は、12:00にオフ、13:59にオン(14:00ちょうどにテレビを
# 使える状態に戻す)、20:00にオフ(20:00は平日も実行する)。12:00〜14:00と20:00以降の「オンできない」側は
# switchbot_service.is_tv_blocked_now がアプリ経由の自動ONを止めることで担保する。
# 本スクリプトは scheduler_boot.py が60秒間隔で起動する(300秒間隔だった頃は起動位相に
# よって14:04などに遅れていた)。
_SLOTS = (
    (2, 0, "turnOff", False),
    (12, 0, "turnOff", True),
    (13, 59, "turnOn", True),
    (20, 0, "turnOff", False),
)
# 平日に動くスロットのうち、リモコン信号(トグル)を送らずプラグのOFFだけにするもの。
# 平日20時は消費電力によるテレビの状態判定が外れると、トグル信号で消えているテレビを
# つけてしまうおそれがあるため、赤外線は送らない。休日の20時は従来どおりリモコンで消してから切る。
_PLUG_ONLY_ON_WEEKDAY = {(20, 0)}


def _run_file_for(hour: int, minute: int) -> str:
    if hour == 2:
        return LAST_RUN_FILE
    return HOLIDAY_RUN_FILE_TEMPLATE.format(hhmm=f"{hour:02d}{minute:02d}")


def main():
    if not config.TV_PLUG_DEVICE_ID:
        logger.debug("TV_PLUG_DEVICE_ID is not set. Skipping.")
        return

    # Issue #592: ホストOSのタイムゾーン設定に依存しないよう、naiveなdatetime.now()
    # ではなく明示的にJSTの現在時刻を使う(この判定はJSTの時刻を意図しており、
    # ホストがJST以外の設定だと別の実時刻に実行されてしまう)。
    now = get_now_jst()

    slot_executed = False
    now_minutes = now.hour * 60 + now.minute
    for hour, minute, command, holiday_only in _SLOTS:
        if not 0 <= now_minutes - (hour * 60 + minute) <= 5:
            continue
        if holiday_only and not is_offday(now):
            continue

        today_str = now.strftime("%Y-%m-%d")
        run_file = _run_file_for(hour, minute)

        # すでに本日実行済みかチェック
        # #661: 読み書きは core/state_file.py(tmp+fsync+os.replace)へ一本化した。
        if state_file.read_text(run_file) == today_str:
            continue  # すでに実行済み

        logger.info(f"📺 [TV Lock] Executing scheduled TV plug command {command} at {hour:02d}:{minute:02d}.")
        slot_executed = True
        try:
            if command == "turnOff" and (hour, minute) in _PLUG_ONLY_ON_WEEKDAY and not is_offday(now):
                # 平日の20時: リモコン信号は送らず、プラグのOFFだけ行う
                res = switchbot_service.send_device_command(config.TV_PLUG_DEVICE_ID, command)
            elif command == "turnOff":
                # テレビがついていれば、先にリモコンで消してからプラグを切る
                res = switchbot_service.turn_off_tv_gracefully()
            else:
                res = switchbot_service.send_device_command(config.TV_PLUG_DEVICE_ID, command)
            if res and res.get("statusCode") == 100:
                logger.info(f"✅ [TV Lock] Successfully sent {command} to TV plug.")

                # 実行完了の記録を保存
                state_file.write_text_atomic(run_file, today_str)
                if command == "turnOff":
                    state_file.write_text_atomic(LAST_CUT_FILE, now.replace(tzinfo=None).isoformat())
            else:
                logger.error(f"❌ [TV Lock] API Error: {res}")
        except Exception as e:
            logger.error(f"❌ [TV Lock] Exception during {command}: {e}")

    if not slot_executed:
        _cut_if_turned_on_while_blocked(now)


def _cut_recently(now) -> bool:
    """スロットでプラグを切ってから RECUT_GRACE_SECONDS 以内ならTrue(記録が無い・壊れていればFalse)。"""
    raw = state_file.read_text(LAST_CUT_FILE)
    if not raw:
        return False
    try:
        cut_at = datetime.datetime.fromisoformat(raw.strip())
    except ValueError:
        return False
    elapsed = (now.replace(tzinfo=None) - cut_at).total_seconds()
    return 0 <= elapsed < RECUT_GRACE_SECONDS


def _cut_if_turned_on_while_blocked(now) -> None:
    """休日の禁止時間帯に、プラグ本体のボタン等で入れ直されていたら再度切る(Issue #900)。

    起動(60秒間隔)ごとに消費電力を確認し、TV_POWER_ON_THRESHOLD_WATTS 以上なら
    リモコン経由で消してからプラグを切る。電力を取得できないときは切らない
    (誤って切るより見逃す方を選ぶ)。通知は送らない(家族との合意事項)。
    """
    if not switchbot_service.is_tv_blocked_now(now):
        return
    # 13:59のturnOnスロットの直後に、解除したばかりのテレビを再度切らない
    if now.hour == 13 and now.minute >= 59:
        return
    # スロットで切った直後は電力値が古いままのことがある(誤判定でトグルを重ねない)
    if _cut_recently(now):
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
