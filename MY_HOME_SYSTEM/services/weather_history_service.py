# MY_HOME_SYSTEM/services/weather_history_service.py
"""日次の気温を Open-Meteo(キー不要の無料API)から取得して `weather_history` へ書き込む。

電気代と気温の分析(`power_analysis_service` / `/dashboard/power`)の気温データの元。
`weather_history` は以前のレガシーな天気取得スクリプトが書いていたが、そのスクリプトが
削除されて以来、書き込む経路が無かった。この関数群と `monitors/weather_monitor.py` が
その後継(気温のみ。降水確率・傘判定の列は書かない)。

- 直近分: `forecast` API(`past_days`で数日前まで遡れる)。毎回、直近 `RECENT_DAYS` 日分を
  更新するので、数日の取得失敗があっても次回の実行で補われる。
- 過去分: `archive` API(`start_date`〜`end_date`)。電力データのある期間まで遡る
  バックフィル用(`monitors/weather_monitor.py --backfill-days N`)。
- 書き込みは「UPDATE → 0行なら INSERT」。`UNIQUE(date, location)` の有無に依存せず、
  他の列(`max_pop`・`umbrella_level`)を上書きしない。

外部通信の失敗は例外にせず `None`/0件で返す(監視スクリプトを落とさない)。
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

import config
import requests
from core.database import get_db_cursor
from core.logger import setup_logging
from core.utils import get_now_iso, get_now_jst

logger = setup_logging("weather_history")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
REQUEST_TIMEOUT_SEC = 20
# 毎回の実行で更新する直近の日数(今日を含む)。取得失敗の取りこぼしを次回で補うための余裕。
RECENT_DAYS = 7
# `forecast` API の `past_days` の上限は92日。バックフィルは1回の取得をこの範囲に収める。
BACKFILL_CHUNK_DAYS = 90
# 気温として妥当な範囲(℃)。範囲外・非数値は欠損として捨てる(APIの異常値で分析を汚さない)。
_TEMP_MIN, _TEMP_MAX = -60.0, 60.0

# WMO weather code → 短い日本語。分類にない値は「その他」。
_WMO_LABELS: tuple[tuple[tuple[int, ...], str], ...] = (
    ((0,), "快晴"),
    ((1,), "晴れ"),
    ((2,), "晴れ時々くもり"),
    ((3,), "くもり"),
    ((45, 48), "霧"),
    ((51, 53, 55, 56, 57), "霧雨"),
    ((61, 63, 65, 66, 67, 80, 81, 82), "雨"),
    ((71, 73, 75, 77, 85, 86), "雪"),
    ((95, 96, 99), "雷雨"),
)


def describe_weather_code(code: Any) -> str | None:
    """WMO weather code を日本語にする。コードが無い・数値でないときは None。"""
    try:
        value = int(code)
    except (TypeError, ValueError):
        return None
    for codes, label in _WMO_LABELS:
        if value in codes:
            return label
    return "その他"


def _valid_temp(value: Any) -> float | None:
    try:
        temp = float(value)
    except (TypeError, ValueError):
        return None
    return temp if math.isfinite(temp) and _TEMP_MIN <= temp <= _TEMP_MAX else None


def parse_daily(payload: Any) -> list[dict[str, Any]]:
    """Open-Meteo の応答(`daily`)を `{date, min_temp, max_temp, weather_desc}` のリストにする。

    形式が想定と違う・日付が不正・最低/最高気温がどちらも欠損の日は捨てる。
    """
    daily = payload.get("daily") if isinstance(payload, dict) else None
    if not isinstance(daily, dict) or not isinstance(daily.get("time"), list):
        return []
    times = daily["time"]
    mins, maxs, codes = (daily.get(k) or [] for k in ("temperature_2m_min", "temperature_2m_max", "weather_code"))
    rows: list[dict[str, Any]] = []
    for i, day in enumerate(times):
        try:
            date.fromisoformat(str(day))
        except ValueError:
            continue
        lo = _valid_temp(mins[i]) if i < len(mins) else None
        hi = _valid_temp(maxs[i]) if i < len(maxs) else None
        if lo is None and hi is None:
            continue
        rows.append({
            "date": str(day), "min_temp": lo, "max_temp": hi,
            "weather_desc": describe_weather_code(codes[i]) if i < len(codes) else None,
        })
    return rows


def _fetch(url: str, params: dict[str, Any]) -> list[dict[str, Any]] | None:
    """APIを呼んで日次データを返す。通信失敗・HTTPエラー・JSON不正は None(警告ログのみ)。"""
    try:
        res = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SEC)
        res.raise_for_status()
        return parse_daily(res.json())
    except (requests.exceptions.RequestException, ValueError) as e:
        logger.warning(f"天気(気温)の取得に失敗しました ({url}): {type(e).__name__}: {e}")
        return None


def _common_params() -> dict[str, Any]:
    return {
        "latitude": config.WEATHER_LATITUDE,
        "longitude": config.WEATHER_LONGITUDE,
        "daily": "temperature_2m_max,temperature_2m_min,weather_code",
        "timezone": "Asia/Tokyo",
    }


def fetch_recent(days: int = RECENT_DAYS) -> list[dict[str, Any]] | None:
    """直近`days`日分(今日を含む)の日次気温を取得する。失敗は None。"""
    days = max(1, min(int(days), 92))
    return _fetch(FORECAST_URL, {**_common_params(), "past_days": days - 1, "forecast_days": 1})


def fetch_range(start: date, end: date) -> list[dict[str, Any]] | None:
    """`start`〜`end`(両端を含む)の日次気温を archive API から取得する。失敗は None。"""
    return _fetch(ARCHIVE_URL, {**_common_params(), "start_date": start.isoformat(), "end_date": end.isoformat()})


def save_rows(rows: list[dict[str, Any]], location: str | None = None) -> int:
    """日次気温を `weather_history` へ書き込む(同じ日付・地点は更新)。書き込んだ日数を返す。

    `weather_desc` は新しい値が無いときに既存の値を消さない。`max_pop`・`umbrella_level`
    には触れない。DBへの書き込みに失敗したら警告ログにして 0 を返す。
    """
    if not rows:
        return 0
    location = location or config.WEATHER_LOCATION_NAME
    now = get_now_iso()
    saved = 0
    try:
        with get_db_cursor(commit=True) as cur:
            for r in rows:
                cur.execute(
                    "UPDATE weather_history SET min_temp = ?, max_temp = ?, "
                    "weather_desc = COALESCE(?, weather_desc), recorded_at = ? WHERE date = ? AND location = ?",
                    (r["min_temp"], r["max_temp"], r["weather_desc"], now, r["date"], location),
                )
                if cur.rowcount == 0:
                    cur.execute(
                        "INSERT INTO weather_history (date, location, min_temp, max_temp, weather_desc, recorded_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (r["date"], location, r["min_temp"], r["max_temp"], r["weather_desc"], now),
                    )
                saved += 1
    except Exception as e:  # noqa: BLE001 (書き込み失敗で監視スクリプトを落とさない)
        logger.warning(f"天気(気温)の保存に失敗しました: {e}")
        return 0
    return saved


def update_recent() -> int:
    """直近`RECENT_DAYS`日分を取得して保存する。保存した日数(取得失敗は 0)。"""
    rows = fetch_recent()
    return save_rows(rows) if rows else 0


def backfill(days: int, today: date | None = None) -> int:
    """`today`(既定は今日)の前日から`days`日さかのぼって取得・保存する。保存した日数の合計。

    archive API には数日分の反映遅れがあるため終端は前日までとし、直近の数日は
    `update_recent` が埋める。`BACKFILL_CHUNK_DAYS` 日ずつ区切って取得し、
    ある区間の取得に失敗しても残りの区間は続ける。
    """
    today = today or get_now_jst().date()
    end = today - timedelta(days=1)
    start = end - timedelta(days=max(int(days), 1) - 1)
    total = 0
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=BACKFILL_CHUNK_DAYS - 1), end)
        rows = fetch_range(cursor, chunk_end)
        if rows:
            total += save_rows(rows)
        cursor = chunk_end + timedelta(days=1)
    return total
