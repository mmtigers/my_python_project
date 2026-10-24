# MY_HOME_SYSTEM/services/dashboard_page_service.py
"""ダッシュボード(かんたん表示)の複数ページ構成。

#829: 以前はStreamlit版の「詳細表示」(`dashboard.py`ほか)とStreamlitを介さない
「かんたん表示」(`/dashboard/m`。ステータスカードのみの単一ページ)が別々に存在した。
詳細表示・表示モード切替UIを廃止し、かんたん表示だけを唯一のダッシュボードとする
方針変更に伴い、ホーム画面からの導線としてカードだけでは足りなかった機能
(カメラのライブ映像・防犯ログ・実家センサーログ・メンテナンス操作)を、
Streamlitに依存しないこのモジュールへ移設する。

`home_status_service.py` と同じく Streamlit を import しない(このサーバー自身が
HTMLを直接返すため、そもそも Streamlit は不要になった)。カードの判定・HTML組み立て
自体は引き続き `home_status_service.py` が正であり、ここはページ全体の組み立てに徹する。
"""
import glob
import html
import json
import os
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

import config
import pandas as pd

from services import home_status_service, system_info_service, timelapse_service

# === ページ共通のシェル ===

# ページ共通のCSS・各ページのJSは`static/dashboard/`の外部ファイル(#949)。Pythonの文字列に埋め込むと
# ESLint等の対象外で、二重エスケープの取り違えが本番で構文エラーになるまで気付けなかったため。
# `{DASHBOARD_BASE_PATH}/static/<ファイル名>`で`routers/dashboard_router.py`が配信する。
_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "dashboard")
# 配信を許可するファイル(許可リスト方式。それ以外は404)。
STATIC_ASSET_FILES: dict[str, str] = {
    "dashboard.css": "text/css; charset=utf-8",
    "camera.js": "text/javascript; charset=utf-8",
    "git_status.js": "text/javascript; charset=utf-8",
    "maintenance.js": "text/javascript; charset=utf-8",
}


def resolve_static_asset(filename: str) -> tuple[str, str] | None:
    """許可リストにある静的ファイル名なら(実パス, Content-Type)を返す。無ければNone。"""
    media_type = STATIC_ASSET_FILES.get(filename)
    if media_type is None:
        return None
    path = os.path.join(_STATIC_DIR, filename)
    return (path, media_type) if os.path.isfile(path) else None


def _static_url(filename: str) -> str:
    """静的ファイルのURL。更新時刻をクエリに付け、ファイルを変えたら端末のキャッシュが即座に切り替わるようにする。"""
    try:
        version = int(os.path.getmtime(os.path.join(_STATIC_DIR, filename)))
    except OSError:
        version = 0
    return f"{config.DASHBOARD_BASE_PATH}/static/{filename}?v={version}"


def _script_tag(filename: str) -> str:
    return f'<script src="{_static_url(filename)}"></script>'




_FOOTER_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("", "🏠", "ホーム"),
    ("watch", "👀", "見守り"),
    ("life", "💡", "くらし"),
    ("sys", "🔧", "システム"),
    ("updates", "📜", "アップデート"),
)


def _render_bottom_nav(dashboard_path: str, current: str | None) -> str:
    """画面下のフッターナビ。`current`はホームなら"home"、他は`_FOOTER_ITEMS`のキー。"""
    base = dashboard_path.rstrip("/")
    items = []
    for key, icon, label in _FOOTER_ITEMS:
        href = html.escape(f"{base}/{key}" if key else f"{base}/")
        is_current = (key or "home") == current
        attr = ' aria-current="page"' if is_current else ""
        items.append(
            f'<li><a href="{href}"{attr}><span class="bn-icon" aria-hidden="true">{icon}</span>'
            f"<span>{html.escape(label)}</span></a></li>"
        )
    return f'<nav class="bottom-nav" aria-label="メインメニュー"><ul>{"".join(items)}</ul></nav>'


def _page_shell(
    title: str,
    body_html: str,
    *,
    extra_head: str = "",
    dashboard_path: str | None = None,
    current: str | None = None,
) -> str:
    """`dashboard_path`を渡すと、画面下にフッターナビを付ける(`current`が現在地)。"""
    footer = _render_bottom_nav(dashboard_path, current) if dashboard_path is not None else ""
    return (
        "<!DOCTYPE html>"
        '<html lang="ja"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        f"<title>{html.escape(title)}</title>"
        f'<link rel="stylesheet" href="{_static_url("dashboard.css")}">'
        f"<style>{home_status_service.STATUS_CARD_CSS}</style>"
        f"{extra_head}"
        "</head><body>"
        f"{body_html}"
        f"{footer}"
        "</body></html>"
    )


def _back_to_home_link(dashboard_path: str, current: str | None = None) -> str:
    """サブページ上部の「ホームへ戻る」と、兄弟ページ(`_NAV_CARDS`)への切替タブ。
    `current`(ページのキー)を渡すとそのタブを現在地として示す。"""
    base = html.escape(dashboard_path.rstrip("/"))
    # f-stringの式の中にバックスラッシュを書けるのはPython 3.12以降(実機・CIは3.11)なので、式の外で作る。
    current_attr = ' aria-current="page"'
    tabs = "".join(
        f'<a href="{base}/{key}"{current_attr if key == current else ""}>{html.escape(label)}</a>'
        for key, label, _ in _NAV_CARDS
    )
    return (
        '<div class="page-head">'
        f'<p><a class="back-link" href="{html.escape(dashboard_path)}">← ホームへ戻る</a></p>'
        f'<nav class="page-tabs" aria-label="ページの切り替え">{tabs}</nav>'
        "</div>"
    )


# === ホームページ ===

# 見守り/くらし/システム/アップデートの各ページへの導線。見守り〜システムはカードの詳細タブ(`tab`)と同じキーを使う。
_NAV_CARDS: tuple[tuple[str, str, str], ...] = (
    ("watch", "👀 見守り", "カメラ・実家の様子"),
    ("life", "💡 くらし", "電気"),
    ("sys", "🔧 システム", "各機能の状態"),
    ("updates", "📜 アップデート", "各アプリの更新履歴"),
)


def _render_external_link(href: str, label: str, healthy: bool | None) -> str:
    """外部リンクカード1枚。`healthy`が True なら緑・「稼働中」、False なら赤・「停止中」、
    None(未判定)なら従来のティールで状態表示なし。タップで遷移できる点は状態に関わらず同じ。"""
    if healthy is None:
        css, state = "external-card", ""
    elif healthy:
        css, state = "external-card external-up", '<span class="link-state">● 稼働中</span>'
    else:
        css, state = "external-card external-down", '<span class="link-state">● 停止中</span>'
    # 別アプリへの遷移なのでダッシュボードを閉じないよう新しいタブで開く(rel=noopenerでwindow.openerを渡さない)
    return (
        f'<a class="{css}" href="{html.escape(href)}" target="_blank" rel="noopener">'
        f"<span>{label}{state}</span><span>›</span></a>"
    )


def _render_external_links(
    quest_path: str,
    asa_note_url: str,
    link_health: dict[str, bool] | None,
    yoru_note_url: str | None = None,
) -> str:
    """「よく使うリンク」(ファミクエ・あさノート・よるノート)。`link_health`は
    `home_status_service.collect_link_health`の戻り値(キー`quest`/`asa_note`/`yoru_note`)。
    よるノートは`yoru_note_url`を渡したときだけ載せる。どのリンクも新しいタブで開く。"""
    health = link_health or {}
    yoru_html = (
        _render_external_link(yoru_note_url, "🌙 よるノート", health.get("yoru_note")) if yoru_note_url else ""
    )
    return (
        "<h2>よく使うリンク</h2>"
        '<div class="link-grid">'
        f'{_render_external_link(quest_path, "⚔️ ファミクエ", health.get("quest"))}'
        f'{_render_external_link(asa_note_url, "📝 あさノート", health.get("asa_note"))}'
        f"{yoru_html}"
        "</div>"
    )


def render_home_page(
    cards,
    fetched_at: datetime,
    *,
    dashboard_path: str,
    status_path: str,
    quest_path: str,
    asa_note_url: str,
    refresh_sec: int,
    manifest_path: str | None = None,
    icon_path: str | None = None,
    link_health: dict[str, bool] | None = None,
    yoru_note_url: str | None = None,
    heatmap_html: str = "",
    climate_html: str = "",
) -> str:
    """ホームページ: ステータスカード + 外部リンク + 今日の動き(ヒートマップ) + 各ページへのナビ。

    **(新機能)** `climate_html`(`render_home_climate_heatmap`の出力。過去30日の温度/湿度)を、
    ステータスカードの「見守り」グループの直下に置く。

    **(新機能)** `heatmap_html`(`render_home_activity_heatmaps`の出力)を、外部リンクの下に置く。
    自動更新の範囲(`STATUS_SECTION_ID`)の内側なので、カードと同じ周期で色も更新される。

    外部リンク(ファミクエ・あさノート)は、以前はナビカードのさらに下、ページ最下部に
    警告表示(`.alerts-warn`)と紛らわしい配色で置かれており、「位置が分かりにくい」
    「色で気づきにくい」の両方の原因になっていた。ステータスカードのすぐ下・
    ナビカードより前に上げ、警告色と被らない配色(`.external-card`)にすることで
    両方を解消する。

    **(新機能)** `link_health`を渡すと、外部リンクを稼働状況で色分けする(緑=稼働中・
    赤=停止中)。リンクは自動更新の対象(`STATUS_SECTION_ID`の内側)に入れてあり、
    色もカードと同じ周期で更新される。
    """
    nav_html = "".join(
        f'<a class="nav-card" href="{dashboard_path.rstrip("/")}/{key}">'
        f"{html.escape(label)}<span class=\"nav-card-sub\">{html.escape(sub)}</span></a>"
        for key, label, sub in _NAV_CARDS
    )
    links_html = _render_external_links(quest_path, asa_note_url, link_health, yoru_note_url) + heatmap_html
    body = (
        "<h1>🏠 おうちの様子</h1>"
        f'{_render_status_section(cards, fetched_at, dashboard_path=dashboard_path, refresh_sec=refresh_sec, extra_html=links_html, climate_html=climate_html)}'
        '<h2>メニュー</h2>'
        f'<div class="link-grid">{nav_html}</div>'
    )
    extra_head = _status_refresh_script(status_path, refresh_sec)
    if manifest_path is not None and icon_path is not None:
        extra_head = _home_screen_install_head(manifest_path, icon_path) + extra_head
    return _page_shell("おうちの様子", body, extra_head=extra_head, dashboard_path=dashboard_path, current="home")


def _home_screen_install_head(manifest_path: str, icon_path: str) -> str:
    """ホーム画面に追加したときアドレスバー無し(standalone)で開くための`<head>`断片。

    マニフェストの取得は既定で認証情報を送らない。このパスは Cloudflare Access の
    内側にあるため `use-credentials` を付けないとホーム画面追加が効かない。
    """
    return (
        f'<link rel="manifest" href="{html.escape(manifest_path)}" crossorigin="use-credentials">'
        f'<link rel="apple-touch-icon" href="{html.escape(icon_path)}">'
        '<meta name="apple-mobile-web-app-capable" content="yes">'
        '<meta name="mobile-web-app-capable" content="yes">'
        '<meta name="theme-color" content="#0d47a1">'
    )


# 自動更新で差し替える範囲のid(ホームページ専用。旧・軽量ページの STATUS_SECTION_ID と同じ名前)。
STATUS_SECTION_ID = home_status_service.STATUS_SECTION_ID


def _render_status_section(
    cards,
    fetched_at: datetime,
    *,
    dashboard_path: str,
    refresh_sec: int,
    extra_html: str = "",
    climate_html: str = "",
) -> str:
    # 温湿度ヒートマップは「見守り」グループの直下に置く
    extras = {"watch": climate_html} if climate_html else None
    grid_html = home_status_service.render_status_grid_html(cards, dashboard_path=dashboard_path, group_extras=extras)
    return (
        f'<div id="{STATUS_SECTION_ID}">'
        f'<p class="meta">{fetched_at.strftime("%m/%d %H:%M:%S")} 時点'
        f"・{int(refresh_sec)}秒ごとに自動更新</p>"
        f"{grid_html}"
        f"{extra_html}"
        "</div>"
    )


def render_home_status_section(
    cards,
    fetched_at: datetime,
    *,
    dashboard_path: str,
    refresh_sec: int,
    quest_path: str | None = None,
    asa_note_url: str | None = None,
    link_health: dict[str, bool] | None = None,
    yoru_note_url: str | None = None,
    heatmap_html: str = "",
    climate_html: str = "",
) -> str:
    """ホームページの自動更新用フラグメント(カードと、外部リンクのブロック、今日の動き)。

    `heatmap_html`はホームページ本体と同じ位置(外部リンクの下)に入れる。

    `quest_path`/`asa_note_url`を渡したときだけ外部リンクを含める(ホームページ本体が
    リンクを自動更新の範囲の中に持つため、差し替え後もリンクが消えないようにする)。
    """
    links_html = (
        _render_external_links(quest_path, asa_note_url, link_health, yoru_note_url)
        if quest_path is not None and asa_note_url is not None
        else ""
    ) + heatmap_html
    return _render_status_section(
        cards, fetched_at, dashboard_path=dashboard_path, refresh_sec=refresh_sec,
        extra_html=links_html, climate_html=climate_html,
    )


def _status_refresh_script(status_path: str, refresh_sec: int) -> str:
    """カードのブロックだけを差し替える自動更新スクリプト(旧・軽量ページと同じ方式)。"""
    return (
        "<script>"
        "(function () {"
        # 温湿度ヒートマップの選択(?hm_loc=&hm_metric=)を自動更新でも引き継ぐ
        f"var url = {json.dumps(status_path)} + window.location.search;"
        f"var intervalMs = {int(refresh_sec) * 1000};"
        "var inFlight = false;"
        "function apply(html) {"
        f'var section = document.getElementById("{STATUS_SECTION_ID}");'
        "if (!section) { return; }"
        "section.outerHTML = html;"
        "}"
        "function update() {"
        "if (inFlight || document.hidden) { return; }"
        "inFlight = true;"
        'fetch(url, { credentials: "same-origin", cache: "no-store" })'
        ".then(function (res) {"
        'if (!res.ok) { throw new Error("status " + res.status); }'
        "return res.text();"
        "})"
        ".then(function (t) { apply(t); })"
        ".catch(function () {"
        f'var section = document.getElementById("{STATUS_SECTION_ID}");'
        'if (section) { section.classList.add("stale"); }'
        "})"
        ".then(function () { inFlight = false; });"
        "}"
        "setInterval(update, intervalMs);"
        'document.addEventListener("visibilitychange", function () {'
        "if (!document.hidden) { update(); }"
        "});"
        "})();"
        "</script>"
    )


# === 見守りページのタイムラプス一覧 ===

_TIMELAPSE_VISIBLE = 10


def _timelapse_label(item: timelapse_service.Timelapse) -> str:
    """「10/16(金) 朝 07:30〜09:00」。時間帯の指定が無い動画は日付だけ。"""
    label = f"{item.day.month}/{item.day.day}({_WEEKDAYS[item.day.weekday()]})"
    if item.start and item.end:
        part = "朝" if item.start < "1200" else "午後"
        label += f" {part} {item.start[:2]}:{item.start[2:]}〜{item.end[:2]}:{item.end[2:]}"
    return label


def _render_timelapse_card(
    item: timelapse_service.Timelapse, camera_names: dict[str, str], url_prefix: str
) -> str:
    base = f"{url_prefix.rstrip('/')}/{quote(item.filename)}"
    video_url = html.escape(base)
    thumb_url = html.escape(base[: -len(".mp4")] + ".jpg")
    thumb = (
        f'<img class="tl-thumb" src="{thumb_url}" alt="" loading="lazy" width="96" height="54">'
        if item.has_thumbnail
        else '<span class="tl-thumb tl-thumb-none" aria-hidden="true">🎞️</span>'
    )
    poster = f' poster="{thumb_url}"' if item.has_thumbnail else ""
    camera = camera_names.get(item.camera, item.camera)
    size_mb = item.size_bytes / (1024 * 1024)
    return (
        '<details class="tl-card"><summary>'
        f"{thumb}"
        f'<span class="tl-meta"><span class="tl-title">{html.escape(_timelapse_label(item))}</span>'
        f'<span class="tl-sub">{html.escape(camera)} ・ {size_mb:.1f}MB</span></span>'
        "</summary>"
        '<div class="tl-body">'
        f'<video controls playsinline preload="none"{poster} src="{video_url}"></video>'
        f'<a class="tl-save" href="{video_url}" download>⬇️ 保存する</a>'
        "</div></details>"
    )


def _render_timelapse_section(
    items: list[timelapse_service.Timelapse],
    *,
    dashboard_path: str,
    camera_names: dict[str, str],
    show_all: bool = False,
    url_prefix: str = timelapse_service.URL_PREFIX,
) -> str:
    """見守りページの「🎞️ タイムラプス」。新しい順で、先頭10件だけ出し、残りは`?tl_all=1`で出す。
    カードをタップするとその場で再生する(動画は開くまで読み込まない)。動画の無いときは案内だけ。"""
    heading = '<div id="timelapse-section"><h2>🎞️ タイムラプス</h2>'
    if not items:
        return heading + '<p class="empty-note">タイムラプスはまだありません(毎日、朝と午後の玄関の動きから自動で作られます)</p></div>'
    shown = items if show_all else items[:_TIMELAPSE_VISIBLE]
    cards = "".join(_render_timelapse_card(i, camera_names, url_prefix) for i in shown)
    watch = f"{html.escape(dashboard_path.rstrip('/'))}/watch"
    more = ""
    if len(items) > _TIMELAPSE_VISIBLE:
        if show_all:
            more = f'<div class="tl-more"><a href="{watch}#timelapse-section">最新{_TIMELAPSE_VISIBLE}件だけにする</a></div>'
        else:
            more = (
                f'<div class="tl-more"><a href="{watch}?tl_all=1#timelapse-section">'
                f"さらに{len(items) - _TIMELAPSE_VISIBLE}件を表示</a></div>"
            )
    return f'{heading}<p class="meta">直近{config.RECORDING_RETENTION_DAYS}日分です。タップすると再生します。</p>{cards}{more}</div>'


# === 見守りページ ===

_SNAPSHOT_GLOB_LIMIT = 20
_SNAPSHOT_GALLERY_LIMIT = 8


def _list_snapshot_files() -> list[str]:
    """NAS上のスナップショット画像のファイル名一覧(新しい順)。

    `config.ASSETS_DIR` はNAS上のパスで、遅延解決(モジュール__getattr__)のため
    アクセス時にNAS障害へ触れうる。ページ全体を落とさないよう例外を握りつぶす
    (`home_status_service._cached`と同じ「1箇所の取得失敗で全体を壊さない」方針)。
    """
    try:
        img_dir = os.path.join(config.ASSETS_DIR, "snapshots")
        paths = sorted(glob.glob(os.path.join(img_dir, "*.jpg")), reverse=True)
        return [os.path.basename(p) for p in paths[:_SNAPSHOT_GLOB_LIMIT]]
    except OSError:
        return []


def resolve_snapshot_path(filename: str) -> str | None:
    """スナップショットの1ファイルを安全に解決する(パストラバーサル対策)。

    見つからない/`filename`が`snapshots`配下からはみ出す場合は None を返す。
    """
    try:
        base_dir = os.path.realpath(os.path.join(config.ASSETS_DIR, "snapshots"))
    except OSError:
        return None
    candidate = os.path.realpath(os.path.join(base_dir, filename))
    if os.path.commonpath([base_dir, candidate]) != base_dir:
        return None
    if not os.path.isfile(candidate):
        return None
    return candidate


# ファイル名末尾の撮影日時(`monitors/camera_monitor.py`の`save_image_from_stream`が
# `{カメラ名}_{種別}_{YYYYMMDD_HHMMSS}.jpg`の形式で付与する)。カメラ名に`_`を含みうる
# ため、位置ではなく末尾のこのパターンで抽出する。
_SNAPSHOT_TIMESTAMP_RE = re.compile(r"_(\d{8}_\d{6})\.jpg$")


def _snapshot_timestamp(filename: str) -> datetime | None:
    """スナップショットのファイル名から撮影日時を取り出す(形式が違えば None)。"""
    m = _SNAPSHOT_TIMESTAMP_RE.search(filename)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")  # noqa: DTZ007
    except ValueError:
        return None


def _render_snapshot_gallery(snapshot_url_prefix: str) -> str:
    """カメラのスナップショット一覧。

    以前は`<img>`を並べるだけで、タップしても拡大できず・いつの写真かも
    分からなかった。各画像を元ファイルへの`<a target="_blank">`にしてタップで
    拡大表示できるようにし、ファイル名から撮影日時を取り出してキャプションに出す。
    """
    files = _list_snapshot_files()[:_SNAPSHOT_GALLERY_LIMIT]
    if not files:
        return '<p class="empty-note">写真なし</p>'
    items = []
    for name in files:
        url = f"{html.escape(snapshot_url_prefix)}/{html.escape(name)}"
        moment = _snapshot_timestamp(name)
        caption = (
            f'<span class="snapshot-caption">'
            f'{html.escape(home_status_service.format_short_timestamp(moment))}</span>'
            if moment is not None else ""
        )
        items.append(
            f'<a class="snapshot-item" href="{url}" target="_blank" rel="noopener">'
            f'<img src="{url}" loading="lazy" alt="スナップショット">'
            f"{caption}</a>"
        )
    return f'<div class="snapshot-grid">{"".join(items)}</div>'


def _render_simple_table(df: pd.DataFrame, columns: dict[str, str], *, limit: int = 50) -> str:
    """スマホ向けの簡易テーブル(列を絞り、時刻は相対表記にする)。"""
    available = {src: label for src, label in columns.items() if src in df.columns}
    if df.empty or not available:
        return '<p class="empty-note">表示できるデータがありません</p>'

    rows = df.head(limit)
    head = "".join(f"<th>{html.escape(label)}</th>" for label in available.values())
    body_rows = []
    for _, row in rows.iterrows():
        cells = []
        for src in available:
            value = row[src]
            if src == "timestamp":
                text = home_status_service.format_relative_time(value)
                if text and home_status_service.format_short_timestamp(value):
                    text = f"{home_status_service.format_short_timestamp(value)} ({text})"
            else:
                text = "" if pd.isna(value) else str(value)
            cells.append(f"<td>{html.escape(text)}</td>")
        body_rows.append(f"<tr>{''.join(cells)}</tr>")
    return f'<table class="simple-table"><thead><tr>{head}</tr></thead><tbody>{"".join(body_rows)}</tbody></table>'


# 見守りページのログ表で常時表示する件数。残りは<details>で折りたたむ。
_LOG_TABLE_VISIBLE_ROWS = 5


def _render_collapsible_log_table(
    df: pd.DataFrame, columns: dict[str, str], *, visible: int = _LOG_TABLE_VISIBLE_ROWS, limit: int = 50
) -> str:
    """`_render_simple_table`を、先頭`visible`件だけ常時表示し残りを`<details>`で
    折りたたむ形に拡張する。

    **(UI改善)** 防犯ログ・センサーログが縦に長く連なりスクロールが大変だった問題の
    改善。全件は引き続き`limit`件まで読み込み、隠れているだけでデータ自体は減らさない。
    """
    if df.empty or not any(col in df.columns for col in columns):
        return _render_simple_table(df, columns, limit=limit)

    head_html = _render_simple_table(df.head(visible), columns, limit=visible)
    rest_df = df.iloc[visible:limit]
    if rest_df.empty:
        return head_html
    rest_html = _render_simple_table(rest_df, columns, limit=len(rest_df))
    return f"{head_html}<details><summary>さらに{len(rest_df)}件を表示</summary>{rest_html}</details>"


def sensor_state_change_log(df: pd.DataFrame) -> pd.DataFrame:
    """センサーログを「開閉/動体センサーの状態が変わった行」だけに絞る(新しい順で返す)。

    **(UI改善)** 見守りページのセンサーログは`location`だけで絞っていたため、温湿度計
    (Meter)や電力プラグ(炊飯器など)が5分おきに残す定期記録も並び、開閉・動体の
    重要なログが埋もれていた。

    - `contact_state`/`movement_state`のどちらも持たない行(温湿度・電力の定期記録)は除く。
    - 残った行も、同じ機器(`device_id`。無ければ`friendly_name`)で直前の行と状態が同じなら除く(最初の行は残す)。
      取得範囲(`load_sensor_data`のlimit)の外にある過去の状態は分からないため、範囲内で
      最も古い行は常に「変化」として残る。
    - **(UI改善)** 人感センサー(SwitchBotのWoPresence)は、検知が終わるたびに`not_detected`も
      記録する(Webhookは検知結果を`contact_state`に入れる)。`detected`と`not_detected`が
      短い間隔で入れ替わると、状態が毎回変わるため上の重複除去では減らず、ログが毎分
      並んでいた。「動きを検知した」ことだけが知りたいので、`not_detected`の行は
      **重複除去のあとで**除く(先に除くと、`detected`→`not_detected`→`detected`の
      2回目の検知が「同じ状態の連続」として消えてしまう)。結果として、人感センサーは
      「検知が始まった行」だけが残る。開閉センサー(`open`/`close`等)・カメラ(`movement_state`)
      には影響しない。
    """
    state_cols = [c for c in ("contact_state", "movement_state") if c in df.columns]
    if df.empty or not state_cols or "timestamp" not in df.columns:
        return df.iloc[0:0]

    states = df[state_cols].apply(lambda col: col.fillna("").astype(str).str.strip())
    kept = df[(states != "").any(axis=1)]
    if kept.empty:
        return kept
    ordered = kept.sort_values("timestamp", kind="stable")
    # 機器の識別は`device_id`(無ければ`friendly_name`)。どちらも無ければ機器を区別できないため
    # 状態の絞り込みだけを行い、重複の除去はしない。
    device_col = next((c for c in ("device_id", "friendly_name") if c in ordered.columns), None)
    if device_col is None:
        return _drop_presence_cleared(ordered).sort_values("timestamp", ascending=False, kind="stable")
    key = states.loc[ordered.index].agg("|".join, axis=1)
    previous = key.groupby(ordered[device_col]).shift()
    changed = previous.isna() | (previous != key)
    return _drop_presence_cleared(ordered[changed]).sort_values("timestamp", ascending=False, kind="stable")


# 人感センサーが「検知が終わった」ときに`contact_state`へ記録する値(Webhookは小文字化して保存する)。
_PRESENCE_CLEARED_STATE = "not_detected"


def _drop_presence_cleared(df: pd.DataFrame) -> pd.DataFrame:
    """人感センサーの「検知なし(`not_detected`)」の行を除く。`sensor_state_change_log`専用。"""
    if "contact_state" not in df.columns:
        return df
    cleared = df["contact_state"].fillna("").astype(str).str.strip().str.lower() == _PRESENCE_CLEARED_STATE
    return df[~cleared]


# 「開 → 閉」を1行にまとめる上限秒数(この秒数ちょうどを含む)。
_OPEN_CLOSE_MERGE_SECONDS = 60


def merge_open_close_events(df: pd.DataFrame) -> pd.DataFrame:
    """開閉センサーの「open」の直後(同じ機器で`_OPEN_CLOSE_MERGE_SECONDS`秒以内)に
    「close」が来た組を、1行(開いた時刻の行)にまとめる。新しい順で返す。

    **(UI改善)** ドアを開けて閉める1回の出入りが、開いた行と閉じた行の2行に分かれて
    ログが長くなっていた。まとめた行の`contact_state`は「開 → 閉（N秒）」にする。

    - まとめるのは「open → close」の向きだけ。「close → open」や`timeoutnotclose`
      (開けっぱなし)、人感センサー(`detected`)は対象外で、そのまま別行で残す。
    - 機器の識別は`sensor_state_change_log`と同じ(`device_id`、無ければ`friendly_name`)。
      「直後」は同じ機器の次の行で、他の機器の行は間に挟まっていても関係しない。
    - `movement_state`が入っている行は(開閉以外の情報を持つため)まとめない。
    - `sensor_state_change_log`の出力(機器ごとに状態が変わった行だけ)を想定する。
    - `timestamp`が日時型でない・必要な列が無い場合は何もせずそのまま返す。
    """
    if (
        df.empty
        or "contact_state" not in df.columns
        or "timestamp" not in df.columns
        or not pd.api.types.is_datetime64_any_dtype(df["timestamp"])
    ):
        return df
    device_col = next((c for c in ("device_id", "friendly_name") if c in df.columns), None)
    if device_col is None:
        return df

    ordered = df.sort_values("timestamp", kind="stable")
    state = ordered["contact_state"].fillna("").astype(str).str.strip().str.lower()
    if "movement_state" in ordered.columns:
        has_movement = ordered["movement_state"].fillna("").astype(str).str.strip() != ""
    else:
        has_movement = pd.Series(False, index=ordered.index)
    device = ordered[device_col]

    next_state = state.groupby(device).shift(-1)
    next_has_movement = has_movement.groupby(device).shift(-1, fill_value=False).astype(bool)
    elapsed = ordered["timestamp"].groupby(device).shift(-1) - ordered["timestamp"]
    pair_start = (
        (state == "open")
        & (next_state == "close")
        & ~has_movement
        & ~next_has_movement
        & (elapsed <= pd.Timedelta(seconds=_OPEN_CLOSE_MERGE_SECONDS))
    )
    if not pair_start.any():
        return df

    # 組の「close」側は、同じ機器の1つ前の行が組の開始である行。
    pair_end = pair_start.groupby(device).shift(1, fill_value=False).astype(bool)

    merged = ordered.copy()
    merged["contact_state"] = merged["contact_state"].astype(object)
    seconds = elapsed.dt.total_seconds().round().astype("Int64")
    for idx in merged.index[pair_start]:
        merged.at[idx, "contact_state"] = f"開 → 閉（{int(seconds.at[idx])}秒）"
    return merged[~pair_end].sort_values("timestamp", ascending=False, kind="stable")


def _render_camera_selector(cameras: list[dict[str, Any]]) -> str:
    """有効なカメラ全台のライブ映像を、初期表示から同時に並べる(切替ボタン方式は廃止)。

    各タイルは名前ラベル付きの`<video data-camera-id>`で、`static/dashboard/camera.js`が読み込み時に
    全タイルのHLSを開始する。ライブ配信のffmpegはカメラごとに独立している。"""
    if not cameras:
        return '<p class="empty-note">カメラが登録されていません</p>'
    tiles = "".join(
        '<div class="camera-tile">'
        f'<div class="camera-name">{html.escape(cam["name"])}</div>'
        '<div class="camera-video-box">'
        f'<video class="camera-video" data-camera-id="{html.escape(cam["id"])}" '
        'muted autoplay playsinline controls></video>'
        "</div></div>"
        for cam in cameras
    )
    return f'<div class="camera-grid">{tiles}</div>'


def _camera_script() -> str:
    """hls.js(CDN)のあとに`camera.js`を読む。実行順序は従来のインライン版と同じ。"""
    return '<script src="https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js"></script>' + _script_tag("camera.js")




def parse_log_date(value: str | None) -> date | None:
    """`?date=YYYY-MM-DD`を`date`にする。未指定・形式不正は None(=絞り込みなし)。"""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _sensor_device_options(*frames: pd.DataFrame) -> list[tuple[str, str]]:
    """センサーログの機器フィルタの選択肢`(値, 表示名)`を、表示名の昇順で返す。
    値は`device_id`(無ければ`friendly_name`)。同じ値は1つにまとめる。"""
    options: dict[str, str] = {}
    for frame in frames:
        if frame.empty or "friendly_name" not in frame.columns:
            continue
        key_col = "device_id" if "device_id" in frame.columns else "friendly_name"
        for key, name in frame[[key_col, "friendly_name"]].drop_duplicates().itertuples(index=False):
            if pd.notna(key) and pd.notna(name) and str(key) not in options:
                options[str(key)] = str(name)
    return sorted(((k, v) for k, v in options.items()), key=lambda kv: kv[1])


def _filter_by_device(df: pd.DataFrame, device: str | None) -> pd.DataFrame:
    """`_sensor_device_options`と同じキー(`device_id`、無ければ`friendly_name`)で機器を絞る。"""
    if not device or df.empty:
        return df
    key_col = "device_id" if "device_id" in df.columns else "friendly_name"
    if key_col not in df.columns:
        return df
    return df[df[key_col].astype(str) == device]


def _render_log_date_filter(
    dashboard_path: str,
    selected_date: date | None,
    device_options: list[tuple[str, str]] | None = None,
    selected_device: str | None = None,
) -> str:
    """防犯ログ・センサーログ共通の絞り込み(GETフォーム。JS不要)。
    送信後にログの位置へ戻れるよう、遷移先にフラグメント`#log-filter`を付ける。

    **(新機能)** 日付に加えて、センサーログを機器(`device_options`)で絞れる。
    日付と機器は組み合わせられ、どちらか一方だけでも絞り込める(防犯ログは日付だけに従う)。
    """
    watch_path = f"{html.escape(dashboard_path.rstrip('/'))}/watch"
    value = selected_date.isoformat() if selected_date else ""
    device_name = dict(device_options or []).get(selected_device or "")
    active = selected_date is not None or device_name is not None
    clear_link = f'<a class="log-filter-clear" href="{watch_path}#log-filter">クリア</a>' if active else ""
    select_html = ""
    if device_options:
        opts = ['<option value="">すべての機器</option>'] + [
            f'<option value="{html.escape(k)}"{" selected" if k == selected_device else ""}>{html.escape(v)}</option>'
            for k, v in device_options
        ]
        select_html = f'<select name="device" aria-label="センサーの機器">{"".join(opts)}</select>'
    parts = []
    if selected_date:
        parts.append(f'{selected_date.strftime("%Y/%m/%d")} のログを全件表示')
    if device_name:
        parts.append(f"センサーログは「{html.escape(device_name)}」だけ表示")
    note = (
        f'<p class="meta">{" ・ ".join(parts)}しています</p>'
        if parts
        else '<p class="meta">日付や機器を選ぶと、ログを絞り込みます</p>'
    )
    return (
        '<div id="log-filter">'
        f'<form class="log-filter" method="get" action="{watch_path}#log-filter">'
        f'<input type="date" name="date" value="{value}" aria-label="ログの日付">'
        f"{select_html}"
        '<button type="submit">絞り込み</button>'
        f"{clear_link}"
        "</form>"
        f"{note}"
        "</div>"
    )


# ヒートマップの色段階の下限(1時間あたりの回数)。0回は「動きなし」。
_HEATMAP_LEVEL_THRESHOLDS = (1, 3, 10)  # 1〜2回=薄い赤 / 3〜9回=中間の赤 / 10回以上=濃い赤
_HEATMAP_LEVEL_LABELS = ("動きなし", "1〜2回", "3〜9回", "10回以上")


def hourly_activity_counts(df: pd.DataFrame, day: date) -> dict[str, list[int]]:
    """`day`(JST)の機器別・時間別(0〜23時)の件数を返す。キーは機器の表示名(無ければ「不明」)。

    入力は`sensor_state_change_log`(+`merge_open_close_events`)の出力を想定する
    (状態が変わった行だけ。開→閉は1行にまとまっている)。1行を1回として数える。
    `timestamp`が日時型でない・必要な列が無い場合は空を返す。
    """
    if df.empty or "timestamp" not in df.columns or "friendly_name" not in df.columns:
        return {}
    if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        return {}
    on_day = df[df["timestamp"].dt.date == day]
    counts: dict[str, list[int]] = {}
    for name, hour in zip(on_day["friendly_name"], on_day["timestamp"].dt.hour):
        key = "不明" if pd.isna(name) else str(name)
        counts.setdefault(key, [0] * 24)[int(hour)] += 1
    return counts


def _total_hourly_counts(df: pd.DataFrame, day: date) -> list[int]:
    """全機器を合算した時間別(0〜23時)の件数。"""
    per_device = hourly_activity_counts(df, day)
    return [sum(c[h] for c in per_device.values()) for h in range(24)]


def _heatmap_level(count: int) -> int:
    """件数 → 色段階(0=なし、1=薄い赤、2=中間、3=濃い赤)。"""
    return sum(count >= t for t in _HEATMAP_LEVEL_THRESHOLDS)


# === ホームの温湿度ヒートマップ(過去30日) ===

CLIMATE_LOCATIONS = ("伊丹", "高砂")
CLIMATE_METRICS = ("temp", "hum", "act")
CLIMATE_DEFAULT_LOCATION = "伊丹"
CLIMATE_DEFAULT_METRIC = "temp"
CLIMATE_DEFAULT_SOURCE = "security"
_CLIMATE_METRIC_LABELS = {"temp": "🌡️ 温度", "hum": "💧 湿度", "act": "🏃 動き"}
_CLIMATE_METRIC_COLUMNS = {"temp": "temp_avg", "hum": "hum_avg"}
_CLIMATE_METRIC_UNITS = {"temp": "℃", "hum": "%"}
# 5段階の境界(各値未満の段階 → 最後だけ以上)。温度=15/20/25/30℃、湿度=30/40/60/70%。
_CLIMATE_THRESHOLDS = {"temp": (15.0, 20.0, 25.0, 30.0), "hum": (30.0, 40.0, 60.0, 70.0)}
_CLIMATE_LEGENDS = {
    "temp": ("〜15℃", "15〜20℃", "20〜25℃", "25〜30℃", "30℃〜"),
    "hum": ("〜30%", "30〜40%", "40〜60%", "60〜70%", "70%〜"),
}
_WEEKDAYS = "月火水木金土日"


def parse_climate_location(value: str | None) -> str:
    """`?hm_loc=`の値。`itami`/`takasago`以外は既定(伊丹)。"""
    return {"itami": "伊丹", "takasago": "高砂"}.get(value or "", CLIMATE_DEFAULT_LOCATION)


def parse_climate_metric(value: str | None) -> str:
    """`?hm_metric=`の値。`temp`/`hum`/`act`以外は既定(温度)。"""
    return value if value in CLIMATE_METRICS else CLIMATE_DEFAULT_METRIC


def parse_climate_source(value: str | None) -> str:
    """`?hm_src=`(動きの対象)の値。`HEATMAP_SOURCES`以外は既定(防犯)。"""
    return value if value in HEATMAP_SOURCES else CLIMATE_DEFAULT_SOURCE


def activity_hourly_grids(df: pd.DataFrame) -> dict[str, dict[str, list[int]]]:
    """動きを`{対象: {日付(YYYY-MM-DD): [0〜23時の回数]}}`に集計する(過去30日の動きヒートマップ用)。

    数え方は「📊 今日の動き」と同じ(`activity_source_frames`: 防犯=カメラの動体検知、高砂・伊丹=
    状態が変わった行を開→閉でまとめたもの)。入力は`analysis_service.load_activity_events`の戻り値。
    記録のない日は含めない(描画側が0回として扱う)。"""
    grids: dict[str, dict[str, list[int]]] = {src: {} for src in HEATMAP_SOURCES}
    if df.empty:
        return grids
    for src, frame in activity_source_frames(df).items():
        if frame.empty or "timestamp" not in frame.columns or not pd.api.types.is_datetime64_any_dtype(frame["timestamp"]):
            continue
        counts = frame.groupby([frame["timestamp"].dt.date, frame["timestamp"].dt.hour]).size()
        for (day, hour), n in counts.items():
            grids[src].setdefault(day.isoformat(), [0] * 24)[int(hour)] = int(n)
    return grids


def _climate_location(device_id: Any, device_name: Any) -> str | None:
    """機器の拠点。`devices.json`の`location`を優先し、無ければ機器名の先頭(`伊丹_`/`高砂_`。
    Nature Remoの保存名)で判定する。どちらでも分からなければ None(ヒートマップに含めない)。"""
    for dev in config.MONITOR_DEVICES:
        if dev.get("id") == device_id and dev.get("location") in CLIMATE_LOCATIONS:
            return dev["location"]
    name = str(device_name or "")
    return next((loc for loc in CLIMATE_LOCATIONS if name.startswith(loc)), None)


def _climate_level(value: float, metric: str) -> int:
    """値 → 色段階(0〜4)。"""
    return sum(value >= t for t in _CLIMATE_THRESHOLDS[metric])


def climate_hourly_grid(df: pd.DataFrame, location: str, metric: str) -> dict[str, list[float | None]]:
    """拠点の`日付 → 0〜23時の平均値(記録なしは None)`。同じ拠点の複数機器は記録数で重み付けして平均する。"""
    column = _CLIMATE_METRIC_COLUMNS[metric]
    required = {"date", "hour", "device_id", "device_name", "n", column}
    if df.empty or not required.issubset(df.columns):
        return {}
    keep = [_climate_location(i, n) == location for i, n in zip(df["device_id"], df["device_name"])]
    part = df[keep].dropna(subset=[column])
    if part.empty:
        return {}
    part = part.assign(_w=part[column] * part["n"])
    grouped = part.groupby(["date", "hour"]).agg(w=("_w", "sum"), n=("n", "sum"))
    grid: dict[str, list[float | None]] = {}
    for (day, hour), row in grouped.iterrows():
        h = int(hour)
        if 0 <= h <= 23 and row["n"] > 0:
            grid.setdefault(str(day), [None] * 24)[h] = float(row["w"] / row["n"])
    return grid


def _climate_switch(
    dashboard_path: str, *, location: str, metric: str, source: str, locations: list[str]
) -> str:
    """指標(温度/湿度/動き)と、その対象(温度・湿度=拠点、動き=防犯/高砂/伊丹)の切り替えリンク(JS不要)。
    選んだ組み合わせは`?hm_loc=&hm_metric=[&hm_src=]`で持つ(`hm_src`は既定の防犯のとき省く)。"""
    base = dashboard_path.rstrip("/") + "/"
    loc_key = {"伊丹": "itami", "高砂": "takasago"}

    def link(label: str, loc: str, met: str, src: str, current: bool) -> str:
        query = {"hm_loc": loc_key[loc], "hm_metric": met}
        if src != CLIMATE_DEFAULT_SOURCE:
            query["hm_src"] = src
        attr = ' aria-current="true"' if current else ""
        return f'<a href="{html.escape(base)}?{html.escape(urlencode(query))}#climate-heatmap"{attr}>{html.escape(label)}</a>'

    if metric == "act":
        targets = [link(_HEATMAP_SOURCE_LABELS[s], location, metric, s, s == source) for s in HEATMAP_SOURCES]
    else:
        targets = [link(loc, loc, metric, source, loc == location) for loc in locations]
    metrics = [link(_CLIMATE_METRIC_LABELS[m], location, m, source, m == metric) for m in CLIMATE_METRICS]
    return (
        f'<div class="climate-switch">{"".join(metrics)}</div>'
        f'<div class="climate-switch">{"".join(targets)}</div>'
    )


def render_home_climate_heatmap(
    df: pd.DataFrame,
    now: datetime,
    *,
    dashboard_path: str,
    location: str = CLIMATE_DEFAULT_LOCATION,
    metric: str = CLIMATE_DEFAULT_METRIC,
    days: int = 30,
    activity: dict[str, dict[str, list[int]]] | None = None,
    source: str = CLIMATE_DEFAULT_SOURCE,
) -> str:
    """ホームの「過去30日の温度/湿度/動き」ヒートマップ。行=日付(新しい日が上)、列=0〜23時。

    温度・湿度は1時間の平均(`df`=`load_hourly_climate`の結果。記録のない時間は枠線だけ)、
    動きは1時間の回数(`activity`=`activity_hourly_grids`の結果。「📊 今日の動き」と同じ4段階の赤。
    動きなしは灰色の0回で、記録なしとは区別しない)。まだ来ていない時間は枠線だけ。
    マスはタップできない閲覧用。温度・湿度は記録のある拠点だけを切り替えに出す(1つも無ければ案内だけ)。
    """
    is_activity = metric == "act"
    present: list[str] = []
    if not is_activity:
        present = [loc for loc in CLIMATE_LOCATIONS if climate_hourly_grid(df, loc, "temp") or climate_hourly_grid(df, loc, "hum")]
    heading = f'<h2 id="climate-heatmap">{_CLIMATE_METRIC_LABELS[metric]}の推移(過去{days}日)</h2>'
    if not is_activity and not present:
        return heading + '<p class="empty-note">温度・湿度の記録がまだありません</p>'
    if not is_activity and location not in present:
        location = present[0]
    grid = (
        (activity or {}).get(source, {}) if is_activity else climate_hourly_grid(df, location, metric)
    )
    unit = _CLIMATE_METRIC_UNITS.get(metric, "")
    today = now.date()
    rows = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        values = grid.get(day.isoformat())
        cells = []
        for hour in range(24):
            future = day == today and hour > now.hour
            value = values[hour] if values is not None else None
            if future:
                cells.append(f'<div class="climate-cell cl-none" title="{day.month}/{day.day} {hour}時台: まだ先です"></div>')
            elif is_activity:
                count = int(value or 0)
                note = f"{count}回" if count else "動きなし"
                cells.append(
                    f'<div class="climate-cell hm-{_heatmap_level(count)}" title="{day.month}/{day.day} {hour}時台: {note}"></div>'
                )
            elif value is None:
                cells.append(f'<div class="climate-cell cl-none" title="{day.month}/{day.day} {hour}時台: 記録なし"></div>')
            else:
                cells.append(
                    f'<div class="climate-cell cl-{_climate_level(value, metric)}" '
                    f'title="{day.month}/{day.day} {hour}時台: {value:.1f}{unit}"></div>'
                )
        label = f"{day.month}/{day.day}({_WEEKDAYS[day.weekday()]})"
        rows.append(f'<div class="climate-row"><span class="climate-day">{label}</span><div class="climate-cells">{"".join(cells)}</div></div>')
    ticks = "".join(f'<span style="left:{h / 24 * 100:.2f}%">{h}</span>' for h in (0, 6, 12, 18)) + '<span style="left:100%">24</span>'
    if is_activity:
        legend = "".join(
            f'<span><i style="background:var(--hm-{lvl})"></i>{html.escape(text)}</span>'
            for lvl, text in enumerate(_HEATMAP_LEVEL_LABELS)
        )
        subtitle = f"{_HEATMAP_SOURCE_LABELS[source]}・1時間ごとの回数(「今日の動き」と同じ数え方)"
    else:
        legend = "".join(
            f'<span><i style="background:var(--cl-{i})"></i>{html.escape(text)}</span>'
            for i, text in enumerate(_CLIMATE_LEGENDS[metric])
        ) + '<span><i style="border:1px dashed var(--border)"></i>記録なし</span>'
        subtitle = f"{location}・1時間ごとの平均(5分おきの記録から)"
    switch = _climate_switch(dashboard_path, location=location, metric=metric, source=source, locations=present)
    return (
        f"{heading}"
        f'<div class="climate">{switch}'
        f'<div class="meta">{html.escape(subtitle)}</div>'
        f'<div class="climate-axis heatmap-axis-ticks">{ticks}</div>'
        f'{"".join(rows)}'
        f'<div class="heatmap-legend">{legend}</div>'
        "</div>"
    )


# 活動ヒートマップ・時間帯詳細の対象。キーは`?src=`の値。
HEATMAP_SOURCES = ("security", "takasago", "itami")
_HEATMAP_SOURCE_LABELS = {"security": "🛡️ 防犯", "takasago": "👵 高砂(実家)", "itami": "🏠 伊丹(自宅)"}
_HEATMAP_SOURCE_ANCHORS = {"security": "security-log", "takasago": "takasago-log", "itami": "itami-log"}
_HEATMAP_SOURCE_COLUMNS = {
    "security": {"timestamp": "検知時刻", "friendly_name": "カメラ"},
    "takasago": {"timestamp": "時刻", "friendly_name": "センサー", "contact_state": "状態"},
    "itami": {"timestamp": "時刻", "friendly_name": "センサー", "movement_state": "動体", "contact_state": "開閉"},
}


def parse_log_hour(value: str | None) -> int | None:
    """`?hour=`を0〜23の整数にする。未指定・範囲外・数値でない値は None(=詳細を出さない)。"""
    try:
        hour = int(value) if value is not None else None
    except ValueError:
        return None
    return hour if hour is not None and 0 <= hour <= 23 else None


def parse_log_source(value: str | None) -> str | None:
    """`?src=`を`HEATMAP_SOURCES`のいずれかにする。それ以外は None。"""
    return value if value in HEATMAP_SOURCES else None


def _hour_detail_href(dashboard_path: str, day: date, hour: int, src: str, device: str | None = None) -> str:
    """ヒートマップのマスの遷移先(見守りページの該当時間の詳細ログ)。JS不要のただのリンク。"""
    query = {"date": day.isoformat(), "hour": str(hour), "src": src}
    if device:
        query["hdev"] = device
    return f"{dashboard_path.rstrip('/')}/watch?{urlencode(query)}#hour-log"


def _render_heatmap_rows(
    rows: list[tuple[str, list[int], Callable[[int], str | None] | None]],
    day: date,
    now: datetime,
    *,
    hint: bool = False,
) -> str:
    """`(行の名前, 0〜23時の件数, 時間→遷移先URL)`の行からヒートマップを描く(純HTML/CSS)。

    遷移先が返る(=リンクにする)のは件数が1以上のマスだけ。今日を表示するとき、まだ来ていない
    時間帯のマスは枠線だけにして「動きなし」と区別する。各マスは色だけに頼らず
    `title`/`aria-label`に回数を持つ。
    """
    current_hour = now.hour if day == now.date() else (24 if day < now.date() else -1)
    row_html = []
    for name, counts, href_for in rows:
        cells = []
        for hour, count in enumerate(counts):
            if hour > current_hour:
                cells.append(f'<div class="heatmap-cell hm-future" title="{hour}時台: まだ先です"></div>')
                continue
            label = f"{hour}時台: {count}回" if count else f"{hour}時台: 動きなし"
            attrs = f'class="heatmap-cell hm-{_heatmap_level(count)}" title="{label}" aria-label="{html.escape(name)} {label}"'
            href = href_for(hour) if href_for and count else None
            if href:
                cells.append(f'<a {attrs} href="{html.escape(href)}"></a>')
            else:
                cells.append(f'<div {attrs} role="img"></div>')
        row_html.append(
            f'<div class="heatmap-row"><div class="heatmap-name" title="{html.escape(name)}">'
            f'{html.escape(name)}</div><div class="heatmap-cells">{"".join(cells)}</div></div>'
        )
    ticks = "".join(
        f'<span style="left:{h / 24 * 100:.2f}%">{h}</span>' for h in (0, 6, 12, 18)
    ) + '<span style="left:100%">24</span>'
    legend = "".join(
        f'<span><i style="background:var(--hm-{lvl})"></i>{html.escape(label)}</span>'
        for lvl, label in enumerate(_HEATMAP_LEVEL_LABELS)
    )
    hint_html = '<p class="heatmap-hint">色のついたマスをタップすると、その時間のログを見られます</p>' if hint else ""
    return (
        f'<div class="heatmap"><div class="meta">{day.strftime("%Y/%m/%d")} の動き(1時間ごとの回数)</div>'
        f'{"".join(row_html)}'
        f'<div class="heatmap-axis-ticks">{ticks}</div>'
        f'<div class="heatmap-legend">{legend}</div>{hint_html}</div>'
    )


def _device_keys(df: pd.DataFrame) -> dict[str, str]:
    """表示名 → 機器の識別子(`device_id`、無ければ`friendly_name`)。`_filter_by_device`と同じキー。"""
    key_col = "device_id" if "device_id" in df.columns else "friendly_name"
    if df.empty or key_col not in df.columns or "friendly_name" not in df.columns:
        return {}
    pairs = df[["friendly_name", key_col]].dropna().drop_duplicates("friendly_name")
    return {str(name): str(key) for name, key in pairs.itertuples(index=False)}


def _render_activity_heatmap(
    df: pd.DataFrame,
    day: date,
    now: datetime,
    *,
    link: Callable[[str | None, int], str] | None = None,
) -> str:
    """機器×0〜23時の活動ヒートマップ(見守りページ用。1機器=1行)。

    `link(機器の識別子, 時)`を渡すと、件数のあるマスがその時間の詳細ログへのリンクになる。
    """
    counts = hourly_activity_counts(df, day)
    if not counts:
        return '<p class="empty-note">この日の動きの記録はありません</p>'
    keys = _device_keys(df)
    rows = []
    for name in sorted(counts):
        device = keys.get(name)
        href_for = (lambda hour, d=device: link(d, hour)) if link else None
        rows.append((name, counts[name], href_for))
    return _render_heatmap_rows(rows, day, now, hint=link is not None)


def activity_source_frames(df: pd.DataFrame, device: str | None = None) -> dict[str, pd.DataFrame]:
    """ヒートマップ・時間帯詳細の元になる、`HEATMAP_SOURCES`ごとの行(表と同じ絞り込み)。

    防犯=カメラの動体検知(`camera_motion_log`)。高砂・伊丹=`location`で絞り、
    `sensor_state_change_log`→(`device`指定時は機器で絞る)→`merge_open_close_events`の順。
    """
    frames = {"security": home_status_service.camera_motion_log(df)}
    for src, location in (("takasago", "高砂"), ("itami", "伊丹")):
        if df.empty or "location" not in df.columns:
            frames[src] = df.iloc[0:0]
        else:
            changes = sensor_state_change_log(df[df["location"] == location])
            frames[src] = merge_open_close_events(_filter_by_device(changes, device))
    return frames


def render_home_activity_heatmaps(df_today: pd.DataFrame, now: datetime, *, dashboard_path: str) -> str:
    """ホームページの「📊 今日の動き」: 防犯・高砂・伊丹を機器を合算した1行ずつにまとめて並べる。

    マスをタップすると、見守りページの該当時間の詳細ログ(`?date=今日&hour=&src=`)へ移る。
    機器別の行は見守りページにある(ホームは一目で分かるよう3行に絞る)。
    """
    day = now.date()
    frames = activity_source_frames(df_today)
    rows = []
    for src in HEATMAP_SOURCES:
        rows.append((
            _HEATMAP_SOURCE_LABELS[src],
            _total_hourly_counts(frames[src], day),
            lambda hour, s=src: _hour_detail_href(dashboard_path, day, hour, s),
        ))
    return f'<h2>📊 今日の動き</h2>{_render_heatmap_rows(rows, day, now, hint=True)}'


def _render_hour_detail(
    df: pd.DataFrame,
    *,
    src: str,
    day: date,
    hour: int,
    device: str | None,
    dashboard_path: str,
) -> str:
    """ヒートマップのマスをタップした先: `day`の`hour`時台のログ(時刻順)。

    件数はヒートマップのマスと同じ元データ(`activity_source_frames`)なので一致する。
    前後の時間・ヒートマップへ戻るリンクを添える(幅の狭いマスで隣を押した場合の訂正用)。
    """
    watch = f"{dashboard_path.rstrip('/')}/watch"
    in_hour = pd.DataFrame()
    if not df.empty and "timestamp" in df.columns and pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        mask = (df["timestamp"].dt.date == day) & (df["timestamp"].dt.hour == hour)
        in_hour = _filter_by_device(df[mask], device).sort_values("timestamp", kind="stable")
    names = in_hour["friendly_name"].dropna().unique() if "friendly_name" in in_hour.columns else []
    device_note = f"・{html.escape(str(names[0]))}" if device and len(names) == 1 else ""

    def _link(label: str, target_hour: int | None) -> str:
        if target_hour is None or not 0 <= target_hour <= 23:
            return ""
        return f'<a href="{html.escape(_hour_detail_href(dashboard_path, day, target_hour, src, device))}">{label}</a>'

    close_href = f"{watch}?{urlencode({'date': day.isoformat()})}#{_HEATMAP_SOURCE_ANCHORS[src]}"
    table = (
        _render_simple_table(in_hour, _HEATMAP_SOURCE_COLUMNS[src], limit=max(len(in_hour), 1))
        if not in_hour.empty
        else '<p class="empty-note">この時間のログはありません</p>'
    )
    return (
        '<div id="hour-log">'
        f"<h2>🔎 {day.strftime('%m/%d')} {hour}時台のログ</h2>"
        f'<p class="meta">{html.escape(_HEATMAP_SOURCE_LABELS[src])}{device_note} ・ {len(in_hour)}件</p>'
        f'<div class="hour-nav">{_link("◀ 前の時間", hour - 1)}{_link("次の時間 ▶", hour + 1)}'
        f'<a href="{html.escape(close_href)}">閉じる</a></div>'
        f"{table}"
        "</div>"
    )


def _watch_sensor_logs(
    df_sensor: pd.DataFrame, selected_device: str | None
) -> tuple[pd.DataFrame, pd.DataFrame, list[tuple[str, str]], str | None]:
    """高砂・伊丹のセンサーログ(状態変化行→機器→開閉の統合)と機器の選択肢を返す。

    戻り値は `(高砂, 伊丹, 機器の選択肢, 有効な selected_device)`。

    **(UI改善)** 高砂・伊丹のセンサーログは`sensor_state_change_log`で、開閉/動体
    センサーの「状態が変わった行」だけに絞る(温湿度計・電力プラグの5分おきの記録は
    ここには出さない。値の確認は各カード/システムページ側にある)。

    **(UI改善)** 開閉センサーの「open」の直後(60秒以内)に「close」が来た組は、
    `merge_open_close_events`で「開 → 閉（N秒）」の1行にまとめる。

    **(新機能)** `selected_device`(`device_id`)を指定すると、高砂・伊丹のセンサーログをその機器だけに絞る
    (防犯ログは対象外)。選択肢にない値は無視し、`None`(全機器)に読み替える。
    """
    if not df_sensor.empty and "location" in df_sensor.columns:
        changes_takasago = sensor_state_change_log(df_sensor[df_sensor["location"] == "高砂"])
        changes_itami = sensor_state_change_log(df_sensor[df_sensor["location"] == "伊丹"])
    else:
        changes_takasago = changes_itami = df_sensor.iloc[0:0]
    # 機器の選択肢は絞り込む前の全機器から作る(選んだ機器だけが残って選び直せなくなるのを防ぐ)。
    device_options = _sensor_device_options(changes_takasago, changes_itami)
    if selected_device not in dict(device_options):
        selected_device = None  # 一覧に無い値(古いブックマーク等)は無視して全機器を表示する
    df_takasago = merge_open_close_events(_filter_by_device(changes_takasago, selected_device))
    df_itami = merge_open_close_events(_filter_by_device(changes_itami, selected_device))
    return df_takasago, df_itami, device_options, selected_device


def _watch_heat_frames(
    df_heatmap: pd.DataFrame | None,
    selected_device: str | None,
    df_camera_motion: pd.DataFrame,
    df_takasago: pd.DataFrame,
    df_itami: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """活動ヒートマップの集計元を、`HEATMAP_SOURCES` の種別ごとに返す。

    **(新機能)** 高砂・伊丹の活動ヒートマップは表示日(`selected_date`、無ければ今日)の全件で集計する。
    日付未指定時の`df_sensor`は直近N件のキャッシュで今日の早い時間帯が欠けうるため、呼び出し側が
    今日分の全件を`df_heatmap`で渡す(省略時は`df_sensor`を使う)。
    """
    # ヒートマップは表と同じ絞り込み(状態変化行→機器→開閉の統合)を、表示日の全件に対して行う。
    # `df_heatmap`が無ければ表と同じ`df_sensor`を使う。
    if df_heatmap is None:
        return {"security": df_camera_motion, "takasago": df_takasago, "itami": df_itami}
    return activity_source_frames(df_heatmap, selected_device)


def _watch_hour_detail_html(
    heat_frames: dict[str, pd.DataFrame],
    day: date,
    *,
    hour: int | None,
    hour_src: str | None,
    hour_device: str | None,
    dashboard_path: str,
) -> str:
    """ヒートマップのマスをタップしたときに、フィルタの下に出す「その時間のログ」。

    ヒートマップの件数のあるマスはリンクで、タップすると`hour`(0〜23)・`hour_src`
    (`HEATMAP_SOURCES`)・`hour_device`(機器の識別子。省略時はその種別の全機器)で、
    その時間のログ(`_render_hour_detail`)を出す。`hour`と`hour_src`のどちらかが無効なら
    空文字を返す。
    """
    if hour is None or hour_src not in HEATMAP_SOURCES:
        return ""
    return _render_hour_detail(
        heat_frames[hour_src],
        src=hour_src,
        day=day,
        hour=hour,
        device=hour_device,
        dashboard_path=dashboard_path,
    )


def render_watch_page(
    df_sensor: pd.DataFrame,
    *,
    dashboard_path: str,
    snapshot_url_prefix: str,
    selected_date: date | None = None,
    selected_device: str | None = None,
    df_heatmap: pd.DataFrame | None = None,
    hour: int | None = None,
    hour_src: str | None = None,
    hour_device: str | None = None,
    timelapses: list[timelapse_service.Timelapse] | None = None,
    show_all_timelapses: bool = False,
) -> str:
    """👀 見守り: カメラのライブ選択・スナップショット・防犯ログ・実家/自宅センサーログ。

    見守りグループの4枚のステータスカード(高砂・伊丹・駐車場・カメラ)は
    すべてこのページの `tab="watch"` を指すため、`home_status_service.build_status_cards`
    がカードごとに付ける `anchor` を使って、タップしたカードの内容が実際に載っている
    セクションまで連れて行く。ここに置くid(`camera-section`/`takasago-log`/
    `itami-log`)を変えるときはカード側の`anchor`も一緒に直すこと。

    防犯ログは以前`security_logs`テーブルを読んでいたが、そこへ書き込むコードが
    存在せず常に空だったため、カメラの動体検知(`home_status_service.camera_motion_log`。
    `df_sensor`から抽出するため既に`apply_friendly_names`済み)を正のデータとして使う。

    **(UI改善)** 3つのログ表(防犯ログ・高砂/伊丹センサーログ)は以前
    `_render_simple_table`で最大50行を常に縦に並べており、スクロールが大変だった。
    `_render_collapsible_log_table`で先頭5件だけを常時表示し、残りは`<details>`で
    折りたたむ。

    高砂・伊丹のセンサーログの絞り込みは`_watch_sensor_logs`、ヒートマップの集計元は
    `_watch_heat_frames`を参照(Issue #954 で分割)。

    **(新機能)** `timelapses`(`timelapse_service.list_timelapses`の結果。新しい順)を渡すと、最近の写真の下に
    「🎞️ タイムラプス」を出す(`show_all_timelapses`で先頭10件の上限を外す)。省略時は動画なしの案内になる。

    **(新機能)** 防犯ログにもカメラ別の活動ヒートマップを置く。件数のあるマスをタップした
    ときの時間帯詳細は`_watch_hour_detail_html`を参照。

    **(新機能)** `selected_date`を指定すると3つのログをその日(JST)に絞り、件数の上限
    (通常50件)を外して指定日の全件を表示する(先頭5件の常時表示+折りたたみは同じ)。
    `df_sensor`は呼び出し側(`dashboard_router`)が日付指定時に指定日の全件を渡す
    前提で、ここでは日付による再フィルタはしない。
    """
    cameras = [
        {"id": cam["id"], "name": cam["name"]}
        for cam in config.CAMERAS
        if cam.get("enabled", True)
    ]
    df_camera_motion = home_status_service.camera_motion_log(df_sensor)
    df_takasago, df_itami, device_options, selected_device = _watch_sensor_logs(df_sensor, selected_device)

    heatmap_now = home_status_service.get_now_jst()
    heatmap_day = selected_date or heatmap_now.date()
    heat_frames = _watch_heat_frames(df_heatmap, selected_device, df_camera_motion, df_takasago, df_itami)

    def _cell_link(src: str) -> Callable[[str | None, int], str]:
        return lambda device, h: _hour_detail_href(dashboard_path, heatmap_day, h, src, device)

    hour_detail_html = _watch_hour_detail_html(
        heat_frames, heatmap_day, hour=hour, hour_src=hour_src, hour_device=hour_device, dashboard_path=dashboard_path
    )

    def _log_table(df: pd.DataFrame, columns: dict[str, str]) -> str:
        # 日付指定時は上限を外して全件(`limit`は`df.iloc[visible:limit]`の上端)。
        if selected_date is None:
            return _render_collapsible_log_table(df, columns)
        return _render_collapsible_log_table(df, columns, limit=max(len(df), _LOG_TABLE_VISIBLE_ROWS))

    body = (
        f"{_back_to_home_link(dashboard_path, 'watch')}"
        "<h1>👀 見守り</h1>"
        '<div id="camera-section">'
        "<h2>🎥 カメラの映像</h2>"
        f"{_render_camera_selector(cameras)}"
        "</div>"
        "<h2>🖼️ 最近の写真</h2>"
        f"{_render_snapshot_gallery(snapshot_url_prefix)}"
        f"{_render_timelapse_section(timelapses or [], dashboard_path=dashboard_path, camera_names={c['id']: c['name'] for c in cameras}, show_all=show_all_timelapses)}"
        f"{_render_log_date_filter(dashboard_path, selected_date, device_options, selected_device)}"
        f"{hour_detail_html}"
    )
    log_sections = (
        ("security-log", "🛡️ 防犯ログ", "security", df_camera_motion,
         {"timestamp": "検知時刻", "friendly_name": "カメラ"}),
        ("takasago-log", "👵 高砂実家のセンサーログ", "takasago", df_takasago,
         {"timestamp": "時刻", "friendly_name": "センサー", "contact_state": "状態"}),
        ("itami-log", "🏠 伊丹(自宅)のセンサーログ", "itami", df_itami,
         {"timestamp": "時刻", "friendly_name": "センサー", "movement_state": "動体", "contact_state": "開閉"}),
    )
    for section_id, title, src, df_log, columns in log_sections:
        body += (
            f'<div id="{section_id}">'
            f"<h2>{title}</h2>"
            f"{_render_activity_heatmap(heat_frames[src], heatmap_day, heatmap_now, link=_cell_link(src))}"
            f"{_log_table(df_log, columns)}"
            "</div>"
        )
    return _page_shell("見守り - おうちの様子", body, extra_head=_camera_script(), dashboard_path=dashboard_path, current="watch")


# === くらしページ ===


def _render_daily_cost_history(rows: list[tuple[Any, int]]) -> str:
    """日別の電気代(概算)を横棒グラフ付きの簡易リストで表示する(新しい順、先頭が今日)。

    **(不具合修正で新設)** 以前は「今月の電気代」カードをタップしても同じカードを
    もう一度表示するだけで詳細と呼べる情報が無かった。直近の日別推移を見せることで
    タップする価値のある詳細にする。
    """
    if not rows:
        return '<p class="empty-note">表示できるデータがありません</p>'
    max_cost = max(cost for _, cost in rows) or 1
    items = []
    for i, (day, cost) in enumerate(rows):
        label = "今日" if i == 0 else day.strftime("%m/%d")
        width_pct = max(2, round(cost / max_cost * 100))
        items.append(
            '<div class="cost-row">'
            f'<span class="cost-date">{html.escape(label)}</span>'
            f'<div class="cost-bar-track"><div class="cost-bar" style="width:{width_pct}%"></div></div>'
            f'<span class="cost-value">{cost:,}円</span>'
            "</div>"
        )
    return f'<div class="cost-history">{"".join(items)}</div>'


def render_life_page(cards, *, dashboard_path: str, daily_cost_rows: list[tuple[Any, int]] | None = None) -> str:
    """💡 くらし: 電気のカードを大きく見せ、日別の電気代推移を詳細として表示する。

    **(不具合修正)** `daily_cost_rows`(`analysis_service.calculate_daily_cost_series`)を
    追加し、電気代カードをタップしても意味のある詳細が無かった不具合を修正した。
    """
    life_cards = [card for card in cards if card.group == "life"]
    body = (
        f"{_back_to_home_link(dashboard_path, 'life')}"
        "<h1>💡 くらし</h1>"
        f"{home_status_service.render_status_grid_html(life_cards, show_group_titles=False)}"
        '<p class="empty-note">今月の電気代はスマートメーターの記録からの概算です。</p>'
        f'<div class="link-grid"><a class="nav-card" href="{html.escape(dashboard_path.rstrip("/"))}/power">'
        '⚡ 電気のくわしい分析<span class="nav-card-sub">月ごとの推移・気温との関係・テレビ</span></a></div>'
        "<h2>📊 日別の電気代(概算)</h2>"
        f"{_render_daily_cost_history(daily_cost_rows or [])}"
    )
    return _page_shell("くらし - おうちの様子", body, dashboard_path=dashboard_path, current="life")


# === システムページ ===

# (ラベル, 最終更新時刻を取り出す関数のキー, 異常とみなす経過分数。Noneは情報表示のみ)
FreshnessRow = tuple[str, datetime | None, int | None]


def _electric_last_updated(df_sensor: pd.DataFrame) -> datetime | None:
    if df_sensor.empty or "device_type" not in df_sensor.columns:
        return None
    df = df_sensor[df_sensor["device_type"].isin(["Nature Remo E Lite", "Plug", "Meter"])]
    if df.empty:
        return None
    return df["timestamp"].max()


def build_freshness_rows(
    df_sensor: pd.DataFrame,
    nas_data: pd.Series | None,
    memory: dict[str, float] | None,
    now: datetime,
) -> list[dict[str, Any]]:
    """システムページの「各機能の最終データ更新時刻」一覧を組み立てる。

    専門用語を避け、閾値を超えたものだけ赤くする(項目3)。カメラ(駐車場)は
    イベント駆動(動きがあった時だけ記録される)ため、`get_camera_status`と同じ理由で
    「更新が無い=異常」とはみなさず、情報行として別枠にする。
    """
    rows: list[dict[str, Any]] = []

    def _add(label: str, at: datetime | None, threshold_min: int | None):
        if at is None:
            rows.append({"label": label, "text": "データがありません", "state": "red" if threshold_min else "info"})
            return
        minutes = (now - at).total_seconds() / 60
        at_text = home_status_service.format_relative_time(at, now)
        if threshold_min is not None and minutes > threshold_min:
            rows.append({"label": label, "text": f"{at_text} (更新が止まっています)", "state": "red"})
        else:
            state = "ok" if threshold_min is not None else "info"
            rows.append({"label": label, "text": at_text, "state": state})

    _add("⚡ 電気・環境の見守り", _electric_last_updated(df_sensor), 30)

    nas_at = None
    if nas_data is not None:
        try:
            nas_at = pd.to_datetime(nas_data["timestamp"], errors="coerce")
            if pd.isna(nas_at):
                nas_at = None
        except (KeyError, TypeError):
            nas_at = None
    _add("🗄️ 保存装置(NAS)", nas_at, 15)

    parking_at = home_status_service.latest_parking_motion_at(df_sensor)
    _add("🚗 駐車場カメラ(参考)", parking_at, None)

    rows.append({
        "label": "🖥️ サーバー本体",
        "text": "正常に応答しています" if memory else "応答を確認できませんでした",
        "state": "ok" if memory else "red",
    })

    return rows


def _render_freshness_rows(rows: list[dict[str, Any]]) -> str:
    state_class = {"red": "freshness-red", "ok": "freshness-ok", "info": "freshness-info"}
    items = "".join(
        f'<div class="freshness-row"><span class="freshness-label">{html.escape(row["label"])}</span>'
        f'<span class="freshness-value {state_class.get(row["state"], "")}">{html.escape(row["text"])}</span></div>'
        for row in rows
    )
    return items


def _render_overall_summary(rows: list[dict[str, Any]]) -> str:
    red_count = sum(1 for row in rows if row["state"] == "red")
    if red_count == 0:
        return '<p class="alerts alerts-ok">✅ すべて正常です</p>'
    return f'<p class="alerts alerts-warn">⚠️ {red_count}件、確認が必要です</p>'


# NASの容量推移グラフのサイズ(viewBox)。
_NAS_CHART_WIDTH = 300
_NAS_CHART_HEIGHT = 120
_NAS_CHART_PAD_X = 30   # 左はY軸ラベル(%)の分を空ける
_NAS_CHART_PAD_TOP = 10
_NAS_CHART_PAD_BOTTOM = 18  # 下はX軸ラベル(日付)の分
# 縦軸が狭すぎると数%の揺れが拡大されて誇張されるため、最低でもこの幅(%)は取る。
_NAS_CHART_MIN_SPAN = 4.0
# 描画する点の上限。1時間おきの記録を90日分描くと2000点を超えるため、時間でまとめて間引く。
_NAS_CHART_MAX_POINTS = 150
# 期間切替の選択肢(日)と既定。ルーターもこの値で入力を絞る。
NAS_HISTORY_DAYS_CHOICES = (7, 30, 90)
NAS_HISTORY_DEFAULT_DAYS = 30
# 増加ペースの予測に必要な最低限の期間(日)。これより短いと日内の増減に引きずられる。
_NAS_FORECAST_MIN_DAYS = 2.0


def compute_nas_forecast(df: pd.DataFrame) -> dict[str, Any] | None:
    """NASの使用量(`used_gb`)の増加ペース(GB/日)と、満杯までの見込み日数を求める。

    期間内の`used_gb`を時刻に対して最小二乗法で直線近似した傾きをペースとする。
    録画の自動削除で使用量がのこぎり状に増減するため、あくまで目安。
    返す辞書: `per_day`(GB/日)、`days_to_full`(増加中で空きがあるとき。それ以外は None)。
    必要な列が無い・有効な点が2未満・期間が`_NAS_FORECAST_MIN_DAYS`日未満のときは None。
    """
    if df.empty or not {"timestamp", "used_gb", "free_gb"} <= set(df.columns):
        return None
    valid = df.assign(
        _t=pd.to_datetime(df["timestamp"], errors="coerce"),
        _used=pd.to_numeric(df["used_gb"], errors="coerce"),
    ).dropna(subset=["_t", "_used"])
    if len(valid) < 2:
        return None
    days = (valid["_t"] - valid["_t"].iloc[0]).dt.total_seconds() / 86400.0
    if days.iloc[-1] - days.iloc[0] < _NAS_FORECAST_MIN_DAYS:
        return None
    x_mean, y_mean = days.mean(), valid["_used"].mean()
    denom = ((days - x_mean) ** 2).sum()
    if denom == 0:
        return None
    per_day = float(((days - x_mean) * (valid["_used"] - y_mean)).sum() / denom)
    free = pd.to_numeric(valid["free_gb"], errors="coerce").iloc[-1]
    days_to_full = None
    if per_day > 0 and not pd.isna(free) and free >= 0:
        days_to_full = float(free) / per_day
    return {"per_day": per_day, "days_to_full": days_to_full}


def _downsample_by_time(df: pd.DataFrame, max_points: int) -> pd.DataFrame:
    """点数が`max_points`を超えるとき、期間を等間隔の時間枠に分け、各枠の最後の1行だけ残す。"""
    if len(df) <= max_points:
        return df
    ts = pd.to_datetime(df["timestamp"])
    span_sec = max((ts.iloc[-1] - ts.iloc[0]).total_seconds(), 1.0)
    # 最後の点はちょうど期間の終端にあたり、そのまま割ると枠が1つ余る(max_points+1個目)ため、収める。
    bucket = ((ts - ts.iloc[0]).dt.total_seconds() // (span_sec / max_points)).astype(int).clip(upper=max_points - 1)
    return df.groupby(bucket, sort=True).tail(1)


def _format_nas_forecast(forecast: dict[str, Any] | None) -> str:
    if forecast is None:
        return '<p class="meta">増加ペース: データ不足(2日分以上の記録が必要です)</p>'
    per_day = forecast["per_day"]
    if forecast["days_to_full"] is None:
        trend = "増えていません" if per_day <= 0 else "計算できません"
        return f'<p class="meta">増加ペース: {per_day:+.1f}GB/日(使用量は{trend})</p>'
    days = forecast["days_to_full"]
    full = f"約{days:.0f}日後" if days < 3650 else "10年以上先"
    return (
        f'<p class="meta">増加ペース: {per_day:+.1f}GB/日 ・ このままなら満杯まで<b>{full}</b>'
        "(直線での目安です。録画の自動削除で変わります)</p>"
    )


def _render_nas_period_links(sys_path: str, days: int) -> str:
    """表示期間の切替リンク(7/30/90日)。JS不要。現在の期間は強調して押せなくする。"""
    items = []
    for choice in NAS_HISTORY_DAYS_CHOICES:
        if choice == days:
            items.append(f'<span class="nas-period-current">{choice}日</span>')
        else:
            items.append(f'<a class="nas-period-link" href="{html.escape(sys_path)}?nas_days={choice}#nas-history">{choice}日</a>')
    return f'<p class="nas-period">表示期間: {" ".join(items)}</p>'


def _render_nas_history_chart(
    df: pd.DataFrame, *, days: int | None = None, sys_path: str | None = None
) -> str:
    """NASの使用率推移を折れ線グラフ(インラインSVG)で表示する。

    **(不具合修正で新設)** NASカードをタップしても容量の履歴を見る手段が無かった。
    `analysis_service.load_nas_history`(`nas_records`、古い順)の`percent`列を描画する。

    **(UI改善)** 以前の描画は「縦軸0-100%固定」「点を時刻でなく件数で等間隔」だったため、
    使用率が数%しか動かないNASでは水平線にしか見えなかった。
    - 縦軸は実測の最小〜最大に余白を足した範囲(最低`_NAS_CHART_MIN_SPAN`%幅)にし、軸ラベル(%)を付ける。
    - 横軸は時刻で配置し、始点・終点の日付を付ける(記録が欠けた区間は線が飛ぶ)。
    - グラフの上に「空き容量・使用率・増加ペース・満杯までの目安」を文字で出す(`compute_nas_forecast`)。
    - `days`と`sys_path`を渡すと、表示期間(7/30/90日)の切替リンクを付ける。
    グラフだけでは正確な値を読み取れないため、`<details>`の詳細テーブル(直近10件)も添える。
    """
    period_html = _render_nas_period_links(sys_path, days) if days is not None and sys_path else ""
    if df.empty or "percent" not in df.columns or len(df) < 2:
        return f'{period_html}<p class="empty-note">表示できるデータがありません</p>'

    plot = df.assign(_pct=pd.to_numeric(df["percent"], errors="coerce")).dropna(subset=["_pct"])
    plot = plot.assign(_t=pd.to_datetime(plot["timestamp"], errors="coerce")).dropna(subset=["_t"])
    if len(plot) < 2:
        return f'{period_html}<p class="empty-note">表示できるデータがありません</p>'
    plot = _downsample_by_time(plot, _NAS_CHART_MAX_POINTS)

    values = plot["_pct"].astype(float)
    w, h = _NAS_CHART_WIDTH, _NAS_CHART_HEIGHT
    x0, x1 = _NAS_CHART_PAD_X, w - 8
    y0, y1 = _NAS_CHART_PAD_TOP, h - _NAS_CHART_PAD_BOTTOM
    lo, hi = float(values.min()), float(values.max())
    pad = max((_NAS_CHART_MIN_SPAN - (hi - lo)) / 2.0, 1.0)
    lo, hi = max(0.0, lo - pad), min(100.0, hi + pad)
    if hi - lo < _NAS_CHART_MIN_SPAN:  # 0%/100%の端に張り付いたときは反対側へ広げる
        lo, hi = (0.0, _NAS_CHART_MIN_SPAN) if lo <= 0 else (100.0 - _NAS_CHART_MIN_SPAN, 100.0)
    t_start, t_end = plot["_t"].iloc[0], plot["_t"].iloc[-1]
    t_span = max((t_end - t_start).total_seconds(), 1.0)

    def _y(v: float) -> float:
        return y1 - (max(lo, min(hi, v)) - lo) / (hi - lo) * (y1 - y0)

    def _x(t) -> float:
        return x0 + (t - t_start).total_seconds() / t_span * (x1 - x0)

    points = " ".join(f"{_x(t):.1f},{_y(v):.1f}" for t, v in zip(plot["_t"], values))
    mid = (lo + hi) / 2.0
    grid = "".join(
        f'<line x1="{x0}" y1="{_y(v):.1f}" x2="{x1}" y2="{_y(v):.1f}" class="nas-chart-grid" />'
        f'<text x="{x0 - 4}" y="{_y(v) + 3:.1f}" text-anchor="end" class="nas-chart-label">{v:.0f}%</text>'
        for v in (hi, mid, lo)
    )
    oldest_at = home_status_service.format_short_timestamp(t_start)
    latest_at = home_status_service.format_short_timestamp(t_end)
    x_labels = (
        f'<text x="{x0}" y="{h - 4}" text-anchor="start" class="nas-chart-label">{html.escape(oldest_at)}</text>'
        f'<text x="{x1}" y="{h - 4}" text-anchor="end" class="nas-chart-label">{html.escape(latest_at)}</text>'
    )
    latest = float(values.iloc[-1])
    svg = (
        f'<svg viewBox="0 0 {w} {h}" class="nas-chart" role="img" '
        f'aria-label="NASの使用率推移。{html.escape(oldest_at)}から{html.escape(latest_at)}まで、'
        f'現在の使用率は{latest:.0f}パーセント">'
        f"{grid}"
        f'<polyline points="{points}" class="nas-chart-line" fill="none" />'
        f"{x_labels}"
        "</svg>"
    )
    free_gb = pd.to_numeric(df["free_gb"], errors="coerce").dropna() if "free_gb" in df.columns else None
    free_text = f"空き {int(free_gb.iloc[-1]):,}GB ・ " if free_gb is not None and not free_gb.empty else ""
    caption = (
        f'<p class="meta"><b>{free_text}現在 {latest:.0f}%</b> ・ {html.escape(oldest_at)} 〜 {html.escape(latest_at)}'
        "(縦軸は実測の範囲に合わせています)</p>"
    )
    detail_table = _render_simple_table(
        df.tail(10).iloc[::-1], {"timestamp": "日時", "percent": "使用率(%)", "free_gb": "空き(GB)"}
    )
    return (
        f"{period_html}{caption}{_format_nas_forecast(compute_nas_forecast(df))}{svg}"
        f"<details><summary>詳細データを見る</summary>{detail_table}</details>"
    )


# アップデートページで最初から開いて見せる件数。これを超える分は<details>で折りたたむ。
_RELEASES_VISIBLE = 20


def _render_release_card(entry: dict[str, Any]) -> str:
    """更新履歴1件。**取得元(外部アプリ)由来の文字列なので、すべてエスケープして出す**。"""
    version = f'<span>v{html.escape(entry["version"])}</span>' if entry.get("version") else ""
    parts = [
        (
            '<div class="release-card">'
            '<div class="release-head">'
            f'<span class="release-badge release-badge-{html.escape(entry["app"])}">{html.escape(entry["app_label"])}</span>'
            f"{version}"
            f'<span class="meta">{html.escape(entry["date"])}</span>'
            "</div>"
        )
    ]
    if entry.get("title"):
        parts.append(f'<div class="release-title">{html.escape(entry["title"])}</div>')
    if entry.get("summary"):
        parts.append(f'<p>{html.escape(entry["summary"])}</p>')
    if entry.get("details"):
        parts.append("<ul>" + "".join(f"<li>{html.escape(d)}</li>" for d in entry["details"]) + "</ul>")
    parts.append("</div>")
    return "".join(parts)


def render_updates_page(
    entries: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    *,
    dashboard_path: str,
    selected_app: str | None = None,
) -> str:
    """📜 アップデート: ダッシュボード・ファミクエ・あさノート・よるノートの更新履歴を、
    日付の新しい順に1本で表示する。

    `entries`/`sources`は`release_history_service.get_release_history`の戻り値。
    `selected_app`(アプリのキー)を渡すとそのアプリだけに絞る(未知の値は無視して全件)。
    取得に失敗したアプリは、画面の上に注意を出す(古い内容があればそれを表示する)。
    """
    known = {s["app"] for s in sources}
    if selected_app not in known:
        selected_app = None
    base = f"{dashboard_path.rstrip('/')}/updates"
    counts = {s["app"]: len(s["entries"]) for s in sources}
    # f-stringの式の中にバックスラッシュを書けるのはPython 3.12以降(実機・CIは3.11)なので、
    # 属性の文字列は式の外で作る。
    active_attr = ' class="active"'
    chips = [f'<a href="{html.escape(base)}"{active_attr if selected_app is None else ""}>すべて({len(entries)})</a>'] + [
        f'<a href="{html.escape(base)}?app={html.escape(s["app"])}"'
        f'{active_attr if selected_app == s["app"] else ""}>'
        f'{html.escape(s["label"])}({counts[s["app"]]})</a>'
        for s in sources
    ]
    warnings = "".join(
        f'<p class="release-warn">⚠️ {html.escape(s["label"])}: {html.escape(s["error"])}</p>'
        for s in sources
        if not s["ok"] and (selected_app is None or selected_app == s["app"])
    )
    shown = [e for e in entries if selected_app is None or e["app"] == selected_app]
    if not shown:
        cards = '<p class="empty-note">表示できる更新履歴がありません</p>'
    else:
        head = "".join(_render_release_card(e) for e in shown[:_RELEASES_VISIBLE])
        rest = shown[_RELEASES_VISIBLE:]
        more = (
            f"<details><summary>さらに{len(rest)}件を表示</summary>{''.join(_render_release_card(e) for e in rest)}</details>"
            if rest
            else ""
        )
        cards = head + more
    body = (
        f"{_back_to_home_link(dashboard_path, 'updates')}"
        "<h1>📜 アップデート</h1>"
        '<p class="meta">ダッシュボード・ファミクエ・あさノート・よるノートの更新履歴を、新しい順にまとめています。</p>'
        f'<div class="release-filter">{"".join(chips)}</div>'
        f"{warnings}"
        f"{cards}"
    )
    return _page_shell("アップデート - おうちの様子", body, dashboard_path=dashboard_path, current="updates")


def _format_boot_time(value: Any) -> str:
    """起動履歴の時刻(ISO8601)を「10/09 21:05」形式にする。読めない値は空文字。"""
    return home_status_service.format_short_timestamp(value)


_BOOT_REASON_BADGE_CLASS = {
    system_info_service.REASON_UPDATE: "badge badge-info",
    system_info_service.REASON_MANUAL: "badge badge-warn",
}


def _render_boot_history_box(boots: list[dict[str, Any]]) -> str:
    """サーバーの起動履歴(新しい順)をカードで表示する。`system_info_service.get_boot_history`の結果を受け取る。

    起動の記録は`unified_server`の起動時に1行ずつ追記される。異常終了は記録できないため、
    「起動した時刻」の履歴であることを注記する。先頭の1件は「最新の起動」として強調する。"""
    if not boots:
        body = '<p class="empty-note">起動の記録はまだありません(この機能の導入後の最初の起動から記録されます)</p>'
    else:
        cards = []
        for i, b in enumerate(boots):
            sha = b.get("commit_sha") or "不明"
            subject = b.get("commit_subject") or ""
            branch = b.get("branch") or ""
            reason = system_info_service.REASON_LABELS.get(b.get("reason") or "", "不明")
            badge_cls = _BOOT_REASON_BADGE_CLASS.get(b.get("reason") or "", "badge")
            latest = '<span class="badge badge-ok">最新の起動</span>' if i == 0 else ""
            branch_html = f'<div class="boot-branch">ブランチ: {html.escape(branch)}</div>' if branch else ""
            cards.append(
                f'<li class="boot-card{" is-latest" if i == 0 else ""}">'
                '<div class="boot-card-head">'
                f'<span class="boot-time">{html.escape(_format_boot_time(b.get("booted_at")))}</span>'
                f"{latest}"
                f'<span class="{badge_cls}">{html.escape(reason)}</span>'
                "</div>"
                f'<div class="boot-commit"><span class="git-commit-sha">{html.escape(str(sha))}</span> {html.escape(subject)}</div>'
                f"{branch_html}"
                "</li>"
            )
        body = (
            f'<ul class="boot-list">{"".join(cards)}</ul>'
            '<p class="meta">起動した時刻の履歴です(停電などの異常終了そのものは記録されません)。</p>'
        )
    return f'<div class="info-box" id="boot-history"><h2>🔁 サーバーの起動履歴</h2>{body}</div>'


def _render_git_status_box() -> str:
    """Gitの状態の枠。中身はページ表示後に`static/dashboard/git_status.js`が`/api/system/git/status`から埋める
    (`git fetch`を含み遅いので、ページ本体では待たない)。"""
    return (
        '<div class="info-box" id="git-status"><h2>🔀 Gitの状態</h2>'
        '<div id="gitStatusBody"><p class="meta">確認中...</p></div></div>'
    )




def _render_log_download_box(log_files: list[Any], *, sys_path: str) -> str:
    """📄 ログのダウンロード。タップで1ファイルずつ保存できる(機密情報は伏せて返る)。"""
    if not log_files:
        items = '<p class="meta">ダウンロードできるログはありません。</p>'
    else:
        rows = "".join(
            f'<li><a href="{html.escape(sys_path)}/logs/{html.escape(f.name)}" download>{html.escape(f.name)}</a>'
            f'<span class="meta"> {f.size / 1024:.0f}KB・{f.mtime.strftime("%m/%d %H:%M")}</span></li>'
            for f in log_files
        )
        items = f"<ul>{rows}</ul>"
    return (
        '<div class="info-box" id="log-download">'
        "<h2>📄 ログのダウンロード</h2>"
        '<p class="meta">タップで保存します。トークン等は伏せて返しますが、氏名やIPアドレスは伏せません。</p>'
        f"{items}"
        "</div>"
    )


def render_sys_page(
    df_sensor: pd.DataFrame,
    nas_data: pd.Series | None,
    nas_history: pd.DataFrame,
    memory: dict[str, float] | None,
    disk: dict[str, float] | None,
    now: datetime,
    *,
    dashboard_path: str,
    nas_days: int = NAS_HISTORY_DEFAULT_DAYS,
    boot_history: list[dict[str, Any]] | None = None,
    log_files: list[Any] | None = None,
) -> str:
    """🔧 システム: 全体サマリー・各機能の最終更新時刻・NASの容量推移・メンテナンス操作。

    **(不具合修正)** 各セクションを`.info-box`で視覚的にグループ化してシステムページを
    見やすくした。NASカード(`id="nas-history"`)のタップ先として、NASの容量推移
    グラフ(`nas_history`)を追加した。

    **(UI改善)** 「今すぐバックアップ」は、実行中のスピナー表示・完了/失敗のトースト・
    最新のバックアップ時刻の表示を持つ(`GET /api/system/backup/status`をJSから参照)。
    最新時刻はNASのファイル列挙が必要なため、ページ描画には含めずJSで非同期に取得する
    (NASが遅くてもシステムページ自体は開ける)。
    """
    rows = build_freshness_rows(df_sensor, nas_data, memory, now)

    disk_line = ""
    if disk and "percent" in disk:
        disk_line = f'<p class="meta">保存容量の使用率: {int(disk["percent"])}%</p>'

    body = (
        f"{_back_to_home_link(dashboard_path, 'sys')}"
        "<h1>🔧 システム</h1>"
        f"{_render_overall_summary(rows)}"
        '<div class="box-grid">'
        f"{_render_git_status_box()}"
        f"{_render_boot_history_box(boot_history or [])}"
        '<div class="info-box">'
        "<h2>各機能の最終更新</h2>"
        f"{_render_freshness_rows(rows)}"
        f"{disk_line}"
        "</div>"
        '<div class="info-box" id="nas-history">'
        "<h2>🗄️ NASの容量推移</h2>"
        f"{_render_nas_history_chart(nas_history, days=nas_days, sys_path=dashboard_path.rstrip('/') + '/sys')}"
        "</div>"
        f"{_render_log_download_box(log_files or [], sys_path=dashboard_path.rstrip('/') + '/sys')}"
        "</div>"
        "<h2>🛠️ メンテナンス</h2>"
        '<div class="maintenance-group">'
        # 日常的に使える安全な操作(バックアップ)を先に、影響の大きい再起動系を後ろにまとめる。
        '<div class="maintenance-box">'
        "<h3>📦 バックアップ</h3>"
        "<p>データベースを今すぐバックアップします。</p>"
        '<p class="meta" id="backupLatest">最新のバックアップ: 確認中...</p>'
        '<button type="button" class="primary" id="backupBtn" onclick="dashboardBackup()">'
        "📦 今すぐバックアップ</button>"
        '<div id="backupResult" class="maintenance-result" role="status" aria-live="polite"></div>'
        "</div>"
        '<p class="alerts alerts-warn">⚠️ 次の2つは再起動を伴います。再起動中はダッシュボードやIoT機器の操作が'
        "一時的に使えなくなり、数分かかることがあります。</p>"
        '<div class="box-grid">'
        '<div class="maintenance-box is-danger">'
        "<h3>⬇️ 最新に更新して再起動</h3>"
        "<p>GitHubの最新を取り込んで、システムを再起動します。"
        "取り込みに失敗した場合は再起動しません(新しい内容が無いときも再起動しません)。</p>"
        '<label class="confirm-row"><input type="checkbox" id="deployConfirm" onchange="'
        'document.getElementById(\'deployBtn\').disabled = !this.checked;">'
        "最新に更新して再起動することを理解しました</label>"
        '<button type="button" class="danger" id="deployBtn" disabled onclick="dashboardDeploy()">'
        "⬇️ 最新に更新して再起動</button>"
        '<div id="deployResult" class="maintenance-result" role="status" aria-live="polite"></div>'
        "</div>"
        '<div class="maintenance-box is-danger">'
        "<h3>🔄 システム再起動</h3>"
        "<p>更新は取り込まず、システムだけを再起動します。</p>"
        '<label class="confirm-row"><input type="checkbox" id="restartConfirm" onchange="'
        'document.getElementById(\'restartBtn\').disabled = !this.checked;">'
        "再起動することを理解しました</label>"
        '<button type="button" class="danger" id="restartBtn" disabled onclick="dashboardRestart()">'
        "🔄 システム再起動</button>"
        '<div id="restartResult" class="maintenance-result"></div>'
        "</div>"
        "</div>"
        "</div>"
    )
    return _page_shell(
        "システム - おうちの様子", body, extra_head=_script_tag("maintenance.js") + _script_tag("git_status.js"),
        dashboard_path=dashboard_path, current="sys",
    )


