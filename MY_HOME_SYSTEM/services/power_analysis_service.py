# MY_HOME_SYSTEM/services/power_analysis_service.py
"""電気代・電力の分析(`/dashboard/power`)。月ごとの推移・日別・気温との関係・テレビの使い方。

元データは `power_usage`(スマートメーターと各プラグの瞬時電力。5分ごと)と
`weather_history`(日次の気温。`monitors/weather_monitor.py` が書く)。

電気代は既存の概算(`analysis_service._calculate_cost_between`)と同じ考え方で出す:
瞬時電力(W)に「直前の記録からの経過時間」を掛けてkWhにし、単価(`config.ELECTRICITY_YEN_PER_KWH`)
を掛ける。経過時間が `MAX_INTERVAL_HOURS` を超える区間(記録の欠落)は数えない。
**日ごとに区切って経過時間を取る**(日をまたぐ区間は数えない)ことも既存の日別と同じで、
月の値は日の値の合計にする。そのため、月初からの累計を一括で数える既存のホームのカードとは
1日あたり1区間(5分)ぶんだけ(約0.3%)ずれることがある。

記録が欠けた日は実際より低く出るため、日ごとに**記録率**(数えられた時間 ÷ その日の経過時間)を
出し、低い日は画面で注意を付ける。気温帯別の集計は、記録率が `MIN_COVERAGE_FOR_STATS` 未満の日と
当日(途中経過)を除く。

この関数群は例外を送出しない(取得失敗はその枠だけ「データなし」にする)。純粋な計算は
DataFrameを受け取る関数に分けてあり、DB取得は `analysis_service` の読み取り関数を使う。
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta
from typing import Any

import config
import pandas as pd
from core.jp_holidays import is_offday
from core.logger import setup_logging
from core.utils import get_now_jst

from services import analysis_service

logger = setup_logging("power_analysis")

# 記録の間隔がこれを超える区間は欠落とみなして数えない(`_calculate_cost_between`と同じ)。
MAX_INTERVAL_HOURS = 1.0
# 月ごとの推移で出す月数(今月を含む)。電力ログの保持は400日なので、それより古い月は欠ける。
MONTHS_SHOWN = 13
# 表示期間の選択肢(日)と既定。ルーターもこの値で入力を絞る。
DAYS_CHOICES = (30, 60, 90)
DEFAULT_DAYS = 30
# 記録率がこれ未満の日は「低めに出ている可能性」と注記し、気温帯別の集計から外す。
MIN_COVERAGE_FOR_STATS = 0.8
# 気温帯別の集計の境界(℃)。平均気温(最低と最高の平均)で分ける。
TEMP_BAND_EDGES = (5, 10, 15, 20, 25, 30)
# テレビ: 点灯が途切れてもこの分数以内に再び点けば、同じ視聴(セッション)とみなす。
SESSION_GAP_MINUTES = 15
REPORT_CACHE_TTL_SEC = 600.0

_cache_lock = threading.Lock()
_cache: dict[int, tuple[float, dict[str, Any]]] = {}


# --------------------------------------------------------------------------
# 純粋な計算(DataFrameを受け取って返す)
# --------------------------------------------------------------------------

def compute_intervals(df: pd.DataFrame) -> pd.DataFrame:
    """瞬時電力の記録から、区間ごとの経過時間(時間)とkWhを求める。

    `device_id`ごと・日(JST)ごとに区切って直前の記録との差を取り(日をまたぐ区間は数えない)、
    `MAX_INTERVAL_HOURS` を超える区間と、各グループの最初の記録(直前が無い)は除く。
    戻り値は`date`/`device_id`/`timestamp`/`power_watts`/`hours`/`kwh`を持つ。必要な列が無ければ空。
    """
    columns = ["date", "device_id", "timestamp", "power_watts", "hours", "kwh"]
    if df.empty or not {"timestamp", "power_watts"} <= set(df.columns):
        return pd.DataFrame(columns=columns)
    out = df.copy()
    if "device_id" not in out.columns:
        out["device_id"] = ""
    out["power_watts"] = pd.to_numeric(out["power_watts"], errors="coerce")
    out = out.dropna(subset=["timestamp", "power_watts"]).sort_values("timestamp")
    out["date"] = out["timestamp"].dt.date
    out["hours"] = out.groupby(["device_id", "date"], dropna=False)["timestamp"].diff().dt.total_seconds() / 3600
    out = out.dropna(subset=["hours"])
    out = out[(out["hours"] > 0) & (out["hours"] <= MAX_INTERVAL_HOURS)]
    out["kwh"] = out["power_watts"] / 1000 * out["hours"]
    return out[columns].reset_index(drop=True)


def daily_energy(intervals: pd.DataFrame, days: list[date], now: datetime, n_devices: int) -> pd.DataFrame:
    """日ごとのkWh・電気代・記録率。`days`の全日を返す(記録の無い日は0・記録率0)。

    記録率 = 数えられた時間 ÷ (その日に経過した時間 × メーターの台数)。当日は0時から`now`までを
    分母にする。台数は期間内に記録のあったスマートメーターの数(最低1)。
    """
    n = max(n_devices, 1)
    grouped = intervals.groupby("date").agg(kwh=("kwh", "sum"), hours=("hours", "sum")) if not intervals.empty else None
    rows = []
    for d in days:
        elapsed = (now - now.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds() / 3600 if d == now.date() else 24.0
        if grouped is not None and d in grouped.index:
            kwh, covered = float(grouped.at[d, "kwh"]), float(grouped.at[d, "hours"])
        else:
            kwh, covered = 0.0, 0.0
        coverage = min(covered / (max(elapsed, 1e-9) * n), 1.0) if covered else 0.0
        rows.append({"date": d, "kwh": kwh, "yen": int(kwh * config.ELECTRICITY_YEN_PER_KWH), "coverage": coverage})
    return pd.DataFrame(rows, columns=["date", "kwh", "yen", "coverage"])


def monthly_totals(daily: pd.DataFrame) -> list[dict[str, Any]]:
    """日別から月ごと(古い順)に合計する。`days_with_data`は記録のあった日数、`days`はその月のカレンダー上の日数
    (今月は今日まで)。`partial`は「記録のある日が月の日数より少ない」月(データの保持期間より前の月・今月を含む)。"""
    if daily.empty:
        return []
    frame = daily.assign(month=[d.strftime("%Y-%m") for d in daily["date"]])
    months = []
    for month, g in frame.groupby("month", sort=True):
        with_data = int((g["coverage"] > 0).sum())
        months.append({
            "month": month, "kwh": float(g["kwh"].sum()), "yen": int(g["kwh"].sum() * config.ELECTRICITY_YEN_PER_KWH),
            "days_with_data": with_data, "days": len(g), "partial": with_data < len(g),
        })
    return months


def month_to_date_comparison(daily: pd.DataFrame, today: date) -> dict[str, Any] | None:
    """今月の「昨日まで」を、先月・前年同月の同じ日数と比べる。

    当日は途中経過で低く見えるため含めない(月の1日なら比べる日が無いので None)。
    先月・前年同月は、その期間の記録がすべて揃っている(欠けた日が無い)ときだけ値を出す。
    """
    span = today.day - 1
    if span < 1 or daily.empty:
        return None
    by_date = {r.date: r for r in daily.itertuples()}

    def _sum(year: int, month: int) -> dict[str, Any] | None:
        try:
            days = [date(year, month, d) for d in range(1, span + 1)]
        except ValueError:
            return None
        found = [by_date.get(d) for d in days]
        rows = [r for r in found if r is not None and r.coverage > 0]
        if len(rows) != len(days):  # 記録の無い日・欠けた日が1日でもあれば比べない
            return None
        kwh = sum(r.kwh for r in rows)
        return {"kwh": kwh, "yen": int(kwh * config.ELECTRICITY_YEN_PER_KWH)}

    last_month = date(today.year, today.month, 1) - timedelta(days=1)
    this = _sum(today.year, today.month)
    return {
        "span_days": span, "this": this,
        "last_month": _sum(last_month.year, last_month.month),
        "last_year": _sum(today.year - 1, today.month),
    }


def join_temperature(daily: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    """日別に気温(最低・最高・平均)を結合する。平均は最低と最高の両方があるときだけ((最低+最高)÷2)。
    気温の無い日は`NaN`。`weather`が空・列不足でも日別はそのまま返す。"""
    out = daily.copy()
    for col in ("min_temp", "max_temp", "mean_temp"):
        out[col] = float("nan")
    if weather.empty or not {"date", "min_temp", "max_temp"} <= set(weather.columns):
        return out
    w = weather.assign(date=pd.to_datetime(weather["date"], errors="coerce").dt.date)
    w = w.dropna(subset=["date"]).drop_duplicates("date", keep="last").set_index("date")
    for i, d in zip(out.index, out["date"]):
        if d in w.index:
            lo, hi = pd.to_numeric(w.at[d, "min_temp"], errors="coerce"), pd.to_numeric(w.at[d, "max_temp"], errors="coerce")
            out.at[i, "min_temp"], out.at[i, "max_temp"] = lo, hi
            if pd.notna(lo) and pd.notna(hi):
                out.at[i, "mean_temp"] = (lo + hi) / 2
    return out


def band_label(index: int) -> str:
    edges = TEMP_BAND_EDGES
    if index == 0:
        return f"〜{edges[0]}℃"
    if index == len(edges):
        return f"{edges[-1]}℃〜"
    return f"{edges[index - 1]}〜{edges[index]}℃"


def temperature_bands(daily_with_temp: pd.DataFrame, today: date) -> list[dict[str, Any]]:
    """平均気温の帯ごとに「1日あたりの使用量・電気代」を平均する(気温の低い帯から)。

    記録率が`MIN_COVERAGE_FOR_STATS`未満の日、当日、平均気温の無い日は集計から外す。
    日数が0の帯も(グラフの軸をそろえるため)返す。
    """
    n_bands = len(TEMP_BAND_EDGES) + 1
    usable = daily_with_temp[
        (daily_with_temp["coverage"] >= MIN_COVERAGE_FOR_STATS)
        & (daily_with_temp["date"] != today)
        & daily_with_temp["mean_temp"].notna()
    ]
    bands = [{"label": band_label(i), "days": 0, "avg_kwh": 0.0, "avg_yen": 0} for i in range(n_bands)]
    for row in usable.itertuples():
        idx = sum(1 for e in TEMP_BAND_EDGES if row.mean_temp >= e)
        bands[idx]["days"] += 1
        bands[idx]["avg_kwh"] += row.kwh
    for b in bands:
        if b["days"]:
            b["avg_kwh"] /= b["days"]
            b["avg_yen"] = int(b["avg_kwh"] * config.ELECTRICITY_YEN_PER_KWH)
    return bands


def tv_analysis(
    rows: pd.DataFrame, days: list[date], threshold_watts: float, total_kwh: float,
) -> dict[str, Any]:
    """テレビ(プラグの消費電力)の使い方を分析する。

    `rows`はテレビのプラグの記録(`load_power_rows`)。**点灯**は消費電力が`threshold_watts`以上の記録
    (その記録の直前からの区間を点灯時間とする。5分間隔なので誤差は±5分程度)。
    **視聴(セッション)**は点灯の記録が`SESSION_GAP_MINUTES`分以内の間隔で続く間(途中で短く消しても同じ視聴)。
    視聴の長さは点灯していた時間の合計。
    戻り値: `daily`(日別の点灯時間・kWh・電気代)/`hourly`(時刻ごとの点灯分、1日あたり。平日・休日別)/
    `sessions`・`longest_minutes`・`longest_at`/`avg_on_watts`/`standby_watts`/`kwh`・`yen`/`share_percent`
    (家全体のうちテレビが占める割合。`total_kwh`が0なら None)。記録が無ければ`has_data: False`。
    """
    iv = compute_intervals(rows)
    iv = iv[iv["date"].isin(set(days))]  # 呼び出し側が期間外の記録を渡しても、合計と日別がずれないよう期間に絞る
    result: dict[str, Any] = {"has_data": not iv.empty, "threshold": threshold_watts}
    if iv.empty:
        return result
    iv = iv.copy()
    iv["on"] = iv["power_watts"] >= threshold_watts

    # 視聴(セッション): 点灯の記録どうしの間隔が`SESSION_GAP_MINUTES`分以内なら同じ視聴。
    # 間隔がそれより長い(または最初の点灯)なら新しい視聴。視聴の長さは点灯していた時間の合計
    # (間の短い消灯は含めない)。
    iv = iv.sort_values("timestamp").reset_index(drop=True)
    on_rows = iv[iv["on"]].copy()
    gap_minutes = on_rows["timestamp"].diff().dt.total_seconds() / 60
    on_rows["session"] = (gap_minutes.isna() | (gap_minutes > SESSION_GAP_MINUTES)).cumsum()
    sessions = on_rows.groupby("session").agg(minutes=("hours", lambda h: h.sum() * 60), ended=("timestamp", "max"))

    off_rows = iv[~iv["on"] & (iv["power_watts"] > 0)]
    result["sessions"] = len(sessions)
    result["longest_minutes"] = float(sessions["minutes"].max()) if len(sessions) else 0.0
    result["longest_at"] = sessions["ended"][sessions["minutes"].idxmax()] if len(sessions) else None
    result["avg_on_watts"] = float(on_rows["power_watts"].mean()) if len(on_rows) else None
    result["standby_watts"] = float(off_rows["power_watts"].median()) if len(off_rows) else None
    kwh = float(iv["kwh"].sum())
    result["kwh"], result["yen"] = kwh, int(kwh * config.ELECTRICITY_YEN_PER_KWH)
    result["share_percent"] = kwh / total_kwh * 100 if total_kwh > 0 else None

    # 日別
    per_day = iv.groupby("date").agg(kwh=("kwh", "sum"))
    per_day["on_hours"] = on_rows.groupby("date")["hours"].sum()
    per_day = per_day.fillna(0.0)
    offday = {d: is_offday(d) for d in set(days) | set(iv["date"])}  # 休日判定は日付ごとに1回だけ
    result["daily"] = [
        {"date": d, "on_hours": float(per_day.at[d, "on_hours"]) if d in per_day.index else 0.0,
         "kwh": float(per_day.at[d, "kwh"]) if d in per_day.index else 0.0,
         "yen": int((per_day.at[d, "kwh"] if d in per_day.index else 0.0) * config.ELECTRICITY_YEN_PER_KWH),
         "offday": offday[d]}
        for d in reversed(days)
    ]

    # 時刻別(1日あたりの点灯分)。平日・休日の日数で割る。
    weekday_days = sum(1 for d in days if not offday[d])
    offday_days = sum(1 for d in days if offday[d])
    hourly = {"weekday": [0.0] * 24, "offday": [0.0] * 24}
    for r in on_rows.itertuples():
        hourly["offday" if offday[r.date] else "weekday"][r.timestamp.hour] += r.hours * 60
    result["hourly"] = {
        "weekday": [m / (weekday_days or 1) for m in hourly["weekday"]],
        "offday": [m / (offday_days or 1) for m in hourly["offday"]],
        "weekday_days": weekday_days,
        "offday_days": offday_days,
    }
    return result


# --------------------------------------------------------------------------
# 取得+組み立て
# --------------------------------------------------------------------------

def _first_of_month(today: date, back: int) -> date:
    """今月の1日から`back`か月前の月の1日。"""
    index = today.year * 12 + (today.month - 1) - back
    return date(index // 12, index % 12 + 1, 1)


def build_power_report(days: int = DEFAULT_DAYS, now: datetime | None = None) -> dict[str, Any]:
    """`/dashboard/power`に出す内容を組み立てる(`REPORT_CACHE_TTL_SEC`のキャッシュ付き)。

    `days`は日別・気温帯別・テレビの対象期間(`DAYS_CHOICES`以外は既定)。月ごとの推移は常に`MONTHS_SHOWN`か月分。
    各枠は独立で、取得や計算に失敗した枠は空(`None`/空リスト/`has_data: False`)になり、他の枠には影響しない。
    """
    days = days if days in DAYS_CHOICES else DEFAULT_DAYS
    key = days
    if now is None:
        with _cache_lock:
            cached = _cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < REPORT_CACHE_TTL_SEC:
            return cached[1]
    report = _build(days, now or get_now_jst())
    if now is None:
        with _cache_lock:
            _cache[key] = (time.monotonic(), report)
    return report


def clear_cache() -> None:
    """キャッシュを空にする(テスト用)。"""
    with _cache_lock:
        _cache.clear()


def _build(days: int, now: datetime) -> dict[str, Any]:
    today = now.date()
    report: dict[str, Any] = {
        "days": days, "unit_price": config.ELECTRICITY_YEN_PER_KWH, "generated_at": now,
        "meter_has_data": False, "monthly": [], "comparison": None, "daily": [], "temp_bands": [],
        "weather": {"has_data": False, "latest_date": None, "location": config.WEATHER_LOCATION_NAME},
        "tv": {"configured": bool(config.TV_PLUG_DEVICE_ID), "has_data": False},
    }
    try:
        range_start_day = _first_of_month(today, MONTHS_SHOWN - 1)
        start = datetime.combine(range_start_day, datetime.min.time(), tzinfo=now.tzinfo)
        meter = analysis_service.load_smart_meter_rows(start, now)
        intervals = compute_intervals(meter)
        n_devices = int(meter["device_id"].nunique()) if "device_id" in meter.columns and not meter.empty else 1
        all_days = [range_start_day + timedelta(days=i) for i in range((today - range_start_day).days + 1)]
        daily_all = daily_energy(intervals, all_days, now, n_devices)
        report["meter_has_data"] = not intervals.empty
        report["monthly"] = monthly_totals(daily_all)
        report["comparison"] = month_to_date_comparison(daily_all, today)

        shown_days = all_days[-days:]
        daily = daily_all[daily_all["date"].isin(shown_days)].reset_index(drop=True)
        weather = analysis_service.load_weather_range(shown_days[0], today, config.WEATHER_LOCATION_NAME)
        if not weather.empty:
            report["weather"].update(has_data=True, latest_date=str(weather["date"].max()))
        joined = join_temperature(daily, weather)
        report["temp_bands"] = temperature_bands(joined, today)
        report["daily"] = joined.sort_values("date", ascending=False).to_dict("records")
        total_kwh = float(daily["kwh"].sum())
    except Exception as e:  # noqa: BLE001 (電力・気温の枠だけ空にして、テレビの枠は続ける)
        logger.error(f"電力分析の組み立てに失敗しました: {type(e).__name__}: {e}")
        total_kwh = 0.0
        shown_days = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]

    if config.TV_PLUG_DEVICE_ID:
        try:
            tv_start = datetime.combine(shown_days[0], datetime.min.time(), tzinfo=now.tzinfo)
            rows = analysis_service.load_power_rows(config.TV_PLUG_DEVICE_ID, tv_start, now)
            tv = tv_analysis(rows, shown_days, config.TV_POWER_ON_THRESHOLD_WATTS, total_kwh)
            report["tv"] = {"configured": True, **tv}
        except Exception as e:  # noqa: BLE001
            logger.error(f"テレビの電力分析に失敗しました: {type(e).__name__}: {e}")
    return report
