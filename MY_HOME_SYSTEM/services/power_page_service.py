# MY_HOME_SYSTEM/services/power_page_service.py
"""⚡ 電気ページ(`/dashboard/power`)のHTML組み立て。月ごとの推移・日別・気温との関係・テレビ。

内容は `power_analysis_service.build_power_report` の戻り値を受け取って描くだけ(計算はしない)。
グラフはインラインSVG(NASの容量グラフと同じ方式。JS・外部ライブラリなし)。ページの外枠とCSSは
`dashboard_page_service` と共通。数値はすべてサーバー側で整形した数値で、外部由来の文字列は出さない。
"""
from __future__ import annotations

import html
from datetime import date, datetime
from typing import Any

import pandas as pd
from core.utils import get_now_jst

from services import dashboard_page_service as page
from services import power_analysis_service as analysis

_CHART_W, _CHART_H = 320, 150
_PAD_L, _PAD_R, _PAD_T, _PAD_B = 6, 6, 18, 24
# 日別の表で最初から開いておく日数。残りは<details>で折りたたむ。
_DAILY_VISIBLE = 10
_WEEKDAYS = "月火水木金土日"


def _yen(value: float) -> str:
    return f"¥{int(value):,}"


def _kwh(value: float) -> str:
    return f"{value:.1f}kWh"


def _diff_percent(current: float, base: float) -> str:
    """基準に対する差(例: +12%)。基準が0なら比べられないので空。"""
    return f"{(current - base) / base * 100:+.0f}%" if base > 0 else ""


def _short_date(d: date, today: date | None = None) -> str:
    text = f"{d.month}/{d.day}({_WEEKDAYS[d.weekday()]})"
    return f"{text}・途中" if today is not None and d == today else text


def svg_bars(values: list[float], labels: list[str], *, titles: list[str], faded: list[bool] | None = None,
             aria: str, value_labels: list[str] | None = None) -> str:
    """棒グラフ(1系列)。値は0を基準に最大値でスケールする。`faded`の棒は薄く描く(一部のデータのみ等)。"""
    n = len(values)
    peak = max(max(values, default=0.0), 1e-9)
    plot_w, plot_h = _CHART_W - _PAD_L - _PAD_R, _CHART_H - _PAD_T - _PAD_B
    slot = plot_w / max(n, 1)
    bar_w = slot * 0.7
    parts = [f'<svg viewBox="0 0 {_CHART_W} {_CHART_H}" class="bar-chart" role="img" aria-label="{html.escape(aria)}">']
    base_y = _PAD_T + plot_h
    parts.append(f'<line x1="{_PAD_L}" y1="{base_y}" x2="{_CHART_W - _PAD_R}" y2="{base_y}" class="bar-axis" />')
    for i, v in enumerate(values):
        h = v / peak * plot_h
        x = _PAD_L + i * slot + (slot - bar_w) / 2
        cls = "bar bar-faded" if faded and faded[i] else "bar"
        parts.append(
            f'<rect x="{x:.1f}" y="{base_y - h:.1f}" width="{bar_w:.1f}" height="{max(h, 0.0):.1f}" class="{cls}">'
            f"<title>{html.escape(titles[i])}</title></rect>"
        )
        if value_labels and n <= 14 and v > 0:
            parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{base_y - h - 3:.1f}" text-anchor="middle" class="bar-label">{html.escape(value_labels[i])}</text>')
        parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{_CHART_H - 8}" text-anchor="middle" class="bar-label">{html.escape(labels[i])}</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_grouped_bars(series: list[list[float]], labels: list[str], *, label_every: int, titles: list[list[str]], aria: str) -> str:
    """棒グラフ(2系列を並べる)。系列0は`bar`、系列1は`bar bar-alt`。同じ目盛りでスケールする。"""
    n = len(labels)
    peak = max((max(s, default=0.0) for s in series), default=0.0)
    peak = max(peak, 1e-9)
    plot_w, plot_h = _CHART_W - _PAD_L - _PAD_R, _CHART_H - _PAD_T - _PAD_B
    slot = plot_w / max(n, 1)
    bar_w = slot * 0.42
    base_y = _PAD_T + plot_h
    parts = [f'<svg viewBox="0 0 {_CHART_W} {_CHART_H}" class="bar-chart" role="img" aria-label="{html.escape(aria)}">',
             f'<line x1="{_PAD_L}" y1="{base_y}" x2="{_CHART_W - _PAD_R}" y2="{base_y}" class="bar-axis" />']
    for i in range(n):
        for s, values in enumerate(series):
            h = values[i] / peak * plot_h
            x = _PAD_L + i * slot + slot * 0.08 + s * bar_w
            cls = "bar" if s == 0 else "bar bar-alt"
            parts.append(f'<rect x="{x:.1f}" y="{base_y - h:.1f}" width="{bar_w:.1f}" height="{max(h, 0.0):.1f}" class="{cls}"><title>{html.escape(titles[s][i])}</title></rect>')
        if i % label_every == 0:
            parts.append(f'<text x="{_PAD_L + i * slot + slot / 2:.1f}" y="{_CHART_H - 8}" text-anchor="middle" class="bar-label">{html.escape(labels[i])}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _period_links(base: str, days: int) -> str:
    items = []
    for choice in analysis.DAYS_CHOICES:
        if choice == days:
            items.append(f'<span class="nas-period-current">{choice}日</span>')
        else:
            items.append(f'<a class="nas-period-link" href="{html.escape(base)}?days={choice}">{choice}日</a>')
    return f'<p class="nas-period">表示期間: {" ".join(items)}</p><p class="meta">日別・気温・テレビに効きます(月ごとの推移は常に{analysis.MONTHS_SHOWN}か月分)</p>'


def _monthly_section(report: dict[str, Any]) -> str:
    monthly = report["monthly"]
    if not report["meter_has_data"] or not monthly:
        return '<p class="empty-note">スマートメーターの記録がまだありません</p>'
    cmp_ = report["comparison"]
    lines = []
    if cmp_ and cmp_["this"]:
        span, this = cmp_["span_days"], cmp_["this"]
        lines.append(f'<p class="power-lead">今月(昨日までの{span}日間): <b>{_yen(this["yen"])}</b>({_kwh(this["kwh"])})</p>')
        for label, key in (("先月の同じ日数", "last_month"), ("去年の同じ月の同じ日数", "last_year")):
            base = cmp_[key]
            if base:
                lines.append(f'<p class="meta">{label}: {_yen(base["yen"])}({_diff_percent(this["yen"], base["yen"]) or "差なし"})</p>')
            else:
                lines.append(f'<p class="meta">{label}: 記録がそろっていないため比べられません</p>')
    else:
        lines.append('<p class="meta">月の途中の比較は、月が始まって2日目から出ます(記録が欠けた月は比べません)</p>')
    labels = [f'{int(m["month"][5:])}月' for m in monthly]
    chart = svg_bars(
        [m["yen"] for m in monthly], labels,
        titles=[f'{m["month"]}: {_yen(m["yen"])} / {_kwh(m["kwh"])}' + ("(記録のある日のみ)" if m["partial"] else "") for m in monthly],
        faded=[m["partial"] for m in monthly],
        value_labels=[f'{m["yen"] // 1000}k' if m["yen"] >= 1000 else str(m["yen"]) for m in monthly],
        aria="月ごとの電気代(概算)の推移",
    )
    rows = "".join(
        f'<tr><td>{html.escape(m["month"])}</td><td>{_yen(m["yen"])}</td><td>{_kwh(m["kwh"])}</td>'
        f'<td>{m["days_with_data"]}/{m["days"]}日</td></tr>' for m in reversed(monthly)
    )
    table = ('<details><summary>月ごとの数字を見る</summary><table class="simple-table"><thead><tr>'
             '<th>月</th><th>電気代</th><th>使用量</th><th>記録のある日</th></tr></thead>'
             f"<tbody>{rows}</tbody></table></details>")
    note = '<p class="meta">薄い棒は、記録が月の一部の日にしか無い月(保持期間より前・今月)です。</p>'
    return "".join(lines) + chart + note + table


def _temp_text(row: dict[str, Any]) -> str:
    lo, hi = row.get("min_temp"), row.get("max_temp")
    if pd.isna(lo) and pd.isna(hi):
        return "—"
    return f'{"?" if pd.isna(lo) else f"{lo:.0f}"}〜{"?" if pd.isna(hi) else f"{hi:.0f}"}℃'


def _daily_section(report: dict[str, Any], today: date) -> str:
    daily = report["daily"]
    if not daily:
        return '<p class="empty-note">表示できるデータがありません</p>'

    def _row(r: dict[str, Any]) -> str:
        coverage = r["coverage"]
        low = coverage < analysis.MIN_COVERAGE_FOR_STATS and r["date"] != today
        note = f'⚠️ 記録 {coverage * 100:.0f}%' if low and coverage > 0 else ("記録なし" if coverage == 0 else "")
        return (f'<tr><td>{html.escape(_short_date(r["date"], today))}</td><td>{html.escape(_temp_text(r))}</td>'
                f'<td>{_yen(r["yen"])}</td><td>{_kwh(r["kwh"])}</td><td>{html.escape(note)}</td></tr>')

    head = '<thead><tr><th>日</th><th>気温</th><th>電気代</th><th>使用量</th><th>記録</th></tr></thead>'
    first = "".join(_row(r) for r in daily[:_DAILY_VISIBLE])
    rest = daily[_DAILY_VISIBLE:]
    out = f'<table class="simple-table">{head}<tbody>{first}</tbody></table>'
    if rest:
        out += (f'<details><summary>さらに{len(rest)}日を表示</summary><table class="simple-table">{head}'
                f'<tbody>{"".join(_row(r) for r in rest)}</tbody></table></details>')
    out += ('<p class="meta">記録が欠けた日は、実際より低く出ます(記録率を「記録」欄に出しています)。'
            '当日は途中経過です。</p>')
    return out


def _temp_section(report: dict[str, Any]) -> str:
    weather = report["weather"]
    if not weather["has_data"]:
        return ('<p class="empty-note">気温のデータがまだありません。実機で '
                '<code>python monitors/weather_monitor.py --backfill-days 400</code> を一度実行すると過去分が入り、'
                '以降は自動で更新されます。</p>')
    bands = report["temp_bands"]
    if not any(b["days"] for b in bands):
        return '<p class="empty-note">気温と電気代がそろった日がまだありません(記録率が低い日・当日は除きます)</p>'
    chart = svg_bars(
        [b["avg_yen"] for b in bands], [b["label"] for b in bands],
        titles=[f'{b["label"]}: 1日あたり{_yen(b["avg_yen"])}・{_kwh(b["avg_kwh"])}({b["days"]}日)' for b in bands],
        faded=[0 < b["days"] < 3 for b in bands],
        value_labels=[_yen(b["avg_yen"]).replace("¥", "") if b["days"] else "" for b in bands],
        aria="平均気温の帯ごとの、1日あたりの電気代",
    )
    head_row = "".join(f'<th>{html.escape(b["label"])}</th>' for b in bands)
    days_row = "".join(f'<td>{b["days"]}日</td>' for b in bands)
    table = (f'<table class="simple-table band-days"><thead><tr>{head_row}</tr></thead>'
             f'<tbody><tr>{days_row}</tr></tbody></table>'
             '<p class="meta">表は、各気温帯に当てはまった日数です(3日未満の帯は、グラフを薄く表示しています)。</p>')
    note = (f'<p class="meta">平均気温(最低と最高の平均)の帯ごとの、1日あたりの電気代です。気温の低い帯・高い帯で高ければ、'
            f'暖房・冷房の影響が見えます。気温の最終更新: {html.escape(str(weather["latest_date"]))}({html.escape(weather["location"])})。</p>')
    return chart + table + note


def _stat(label: str, value: str) -> str:
    return f'<div class="power-stat"><div class="power-stat-value">{html.escape(value)}</div><div class="meta">{html.escape(label)}</div></div>'


def _tv_section(report: dict[str, Any]) -> str:
    tv, days = report["tv"], report["days"]
    if not tv["configured"]:
        return '<p class="empty-note">テレビのプラグ(<code>TV_PLUG_DEVICE_ID</code>)が設定されていません</p>'
    if not tv["has_data"]:
        return ('<p class="empty-note">テレビのプラグの消費電力の記録がありません。そのプラグが '
                '<code>devices.json</code> の監視対象に入っているか確認してください。</p>')
    daily = tv["daily"]
    on_total = sum(r["on_hours"] for r in daily)
    longest = tv["longest_minutes"]
    longest_text = f'{int(longest // 60)}時間{int(longest % 60)}分' if longest >= 60 else f"{int(longest)}分"
    if tv.get("longest_at") is not None:
        ended = tv["longest_at"]
        longest_text += f'({ended.month}/{ended.day} {ended.hour}時ごろまで)'
    share = f'{tv["share_percent"]:.1f}%' if tv.get("share_percent") is not None else "—"
    stats = "".join([
        _stat(f"1日あたりの点灯時間({days}日平均)", f"{on_total / days:.1f}時間"),
        _stat(f"電気代({days}日間)", _yen(tv["yen"])),
        _stat("家全体に占める割合", share),
        _stat("視聴の回数", f'{tv["sessions"]}回'),
        _stat("いちばん長い視聴", longest_text),
        _stat("点灯中の平均電力", f'{tv["avg_on_watts"]:.0f}W' if tv.get("avg_on_watts") is not None else "—"),
        _stat("待機電力", f'{tv["standby_watts"]:.1f}W' if tv.get("standby_watts") is not None else "—"),
    ])
    hourly = tv["hourly"]
    chart = svg_grouped_bars(
        [hourly["weekday"], hourly["offday"]], [f"{h}" for h in range(24)], label_every=3,
        titles=[[f'{h}時台・平日: 1日あたり{hourly["weekday"][h]:.0f}分' for h in range(24)],
                [f'{h}時台・休日: 1日あたり{hourly["offday"][h]:.0f}分' for h in range(24)]],
        aria="時間帯ごとの、1日あたりのテレビの点灯時間(平日と休日)",
    )
    legend = (f'<p class="meta"><span class="legend-box bar"></span>平日({hourly["weekday_days"]}日) '
              f'<span class="legend-box bar-alt"></span>休日({hourly["offday_days"]}日) ・ 横軸は時刻(時)、縦軸は1日あたりの点灯分</p>')
    rows = "".join(
        f'<tr><td>{html.escape(_short_date(r["date"]))}{"・休" if r["offday"] else ""}</td>'
        f'<td>{r["on_hours"]:.1f}時間</td><td>{_kwh(r["kwh"])}</td><td>{_yen(r["yen"])}</td></tr>' for r in daily
    )
    table = ('<details><summary>日別の数字を見る</summary><table class="simple-table"><thead><tr>'
             '<th>日</th><th>点灯</th><th>使用量</th><th>電気代</th></tr></thead>'
             f"<tbody>{rows}</tbody></table></details>")
    note = (f'<p class="meta">点灯は消費電力が{tv["threshold"]:.0f}W以上の記録(5分ごとの記録なので誤差は±5分程度)。'
            f'点灯の間隔が{analysis.SESSION_GAP_MINUTES}分以内なら同じ視聴として数えます(視聴の長さは点灯していた時間の合計)。</p>')
    return f'<div class="power-stats">{stats}</div>{chart}{legend}{table}{note}'


def render_power_page(report: dict[str, Any], *, dashboard_path: str, now: datetime | None = None) -> str:
    """⚡ 電気ページ全体。`report`は`power_analysis_service.build_power_report`の戻り値。"""
    generated = report.get("generated_at") or now
    today = generated.date() if generated is not None else get_now_jst().date()
    base = f"{dashboard_path.rstrip('/')}/power"
    body = (
        f"{page._back_to_home_link(dashboard_path)}"
        "<h1>⚡ 電気</h1>"
        f'<p class="meta">スマートメーターの記録からの概算です(単価 {report["unit_price"]:g}円/kWh の固定。'
        "契約プランや燃料費調整は考慮していません)。</p>"
        f"{_period_links(base, report['days'])}"
        '<div class="info-box"><h2>📈 月ごとの推移</h2>'
        f"{_monthly_section(report)}</div>"
        f'<div class="info-box"><h2>📅 日別の電気代({report["days"]}日)</h2>'
        f"{_daily_section(report, today)}</div>"
        '<div class="info-box"><h2>🌡️ 気温との関係</h2>'
        f"{_temp_section(report)}</div>"
        '<div class="info-box"><h2>📺 テレビの使い方</h2>'
        f"{_tv_section(report)}</div>"
    )
    return page._page_shell("電気 - おうちの様子", body)
