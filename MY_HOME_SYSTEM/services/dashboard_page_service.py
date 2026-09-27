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
from datetime import datetime
from typing import Any

import config
import pandas as pd

from services import home_status_service

# === ページ共通のシェル ===

_PAGE_BASE_CSS = """
    :root { color-scheme: light dark; }
    body {
        margin: 0;
        padding: 12px 12px 32px;
        background: #ffffff;
        color: #222;
        font-family: "Helvetica Neue", Arial, "Hiragino Kaku Gothic ProN", "Hiragino Sans", Meiryo, sans-serif;
    }
    h1 { font-size: 1.25rem; margin: 0 0 2px; }
    h2 { font-size: 1rem; margin: 20px 0 8px; }
    .meta { font-size: 0.8rem; color: #666; margin: 0 0 12px; }

    .alerts {
        margin: 0 0 10px; padding: 8px 10px; border-radius: 10px;
        font-size: 0.85rem; line-height: 1.5;
    }
    .alerts-warn { background: #fff3e0; color: #e65100; border: 1px solid #ffe0b2; }
    .alerts-ok { background: #f1f8e9; color: #558b2f; border: 1px solid #dcedc8; }
    .alerts a { color: inherit; font-weight: bold; display: inline-flex; align-items: center; min-height: 44px; }

    /* 自動更新が失敗したときの見た目(取れなかったら古い表示を消さずに薄く残す)。 */
    #status.stale { opacity: 0.55; }
    #status.stale::after {
        content: "⚠️ 更新できていません(表示は最後に取得できた内容です)";
        display: block; margin-top: 8px; font-size: 0.8rem; color: #b71c1c;
    }

    /* ホームへ戻る・各種ナビ */
    nav.top-nav { margin: 0 0 16px; }
    nav.top-nav a, a.back-link {
        display: inline-flex; align-items: center; min-height: 44px;
        padding: 0 14px; border-radius: 12px; border: 1px solid #bbdefb;
        background: #e3f2fd; color: #1565c0; font-weight: bold;
        text-decoration: none; font-size: 0.9rem;
    }

    /* ホーム画面の「見守り/くらし/システム」大きめナビカードと、
       ファミクエ・あさノートの外部リンクカード。ステータスカードと似た見た目にしつつ、
       「タップで移動する」ことが分かるよう矢印を添える。 */
    .link-grid {
        display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
        gap: 8px; margin: 8px 0 4px;
    }
    a.nav-card {
        display: flex; flex-direction: column; justify-content: center; align-items: center;
        gap: 4px; padding: 16px 8px; min-height: 72px; border-radius: 12px;
        text-decoration: none; font-weight: bold; font-size: 1rem;
        background: #e8eaf6; color: #283593; border: 1px solid #c5cae9;
        -webkit-tap-highlight-color: rgba(0,0,0,0.08);
    }
    a.nav-card:active { transform: scale(0.98); }
    a.nav-card .nav-card-sub { font-size: 0.7rem; font-weight: normal; opacity: 0.75; }
    /* #829の見直し: 以前は警告表示(.alerts-warn)と紛らわしいアンバー系の配色で、
       ページ最下部にあったため「位置が分かりにくい」「警告と見分けがつかない」の
       両方の原因になっていた。警告色(オレンジ/黄)ともナビカード(藍)とも被らない
       ティール系の配色にし、ステータスカードのすぐ下(ナビカードより前)に置く。 */
    a.external-card {
        display: flex; align-items: center; justify-content: space-between;
        gap: 8px; padding: 14px 16px; min-height: 44px; border-radius: 12px;
        text-decoration: none; font-weight: bold; font-size: 0.95rem;
        background: #e0f2f1; color: #00695c; border: 1px solid #80cbc4;
        -webkit-tap-highlight-color: rgba(0,0,0,0.08);
    }

    /* 見守りページの簡易テーブル(防犯ログ・実家センサーログ)。
       スマホでは横スクロールさせず、列を絞って縦に収める。 */
    table.simple-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; margin-bottom: 16px; }
    table.simple-table th, table.simple-table td {
        text-align: left; padding: 6px 4px; border-bottom: 1px solid #eee;
    }
    table.simple-table th { color: #666; font-weight: bold; font-size: 0.75rem; }
    .empty-note { color: #888; font-size: 0.85rem; margin: 4px 0 16px; }

    /* カメラのスナップショットギャラリー。タップで元画像を別タブに拡大表示する
       (`.snapshot-item`が`<a target="_blank">`)。 */
    .snapshot-grid {
        display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
        gap: 6px; margin-bottom: 16px;
    }
    a.snapshot-item {
        display: block; text-decoration: none; color: inherit;
        -webkit-tap-highlight-color: rgba(0,0,0,0.08);
    }
    .snapshot-item img { width: 100%; border-radius: 8px; display: block; background: #eee; }
    .snapshot-caption {
        display: block; margin-top: 2px; font-size: 0.7rem; color: #666; text-align: center;
    }

    /* くらしページの電気代詳細(日別推移の簡易バーグラフ) */
    .cost-history { margin-bottom: 16px; }
    .cost-row {
        display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 0.85rem;
    }
    .cost-date { flex: none; width: 3.4em; color: #666; }
    .cost-bar-track {
        flex: 1; height: 10px; border-radius: 5px; background: #eee; overflow: hidden;
    }
    .cost-bar { height: 100%; border-radius: 5px; background: #1565c0; }
    .cost-value { flex: none; width: 4.6em; text-align: right; font-weight: bold; }

    /* システムページの各セクションを視覚的にグループ化する箱
       (不具合修正: 以前は見出しと一覧が地続きで読みにくかった)。 */
    .info-box {
        border: 1px solid #eee; border-radius: 12px; padding: 12px; margin-bottom: 16px;
    }
    .info-box > h2:first-child { margin-top: 0; }

    /* システムページの機能ごとの鮮度一覧 */
    .freshness-row {
        display: flex; justify-content: space-between; align-items: baseline;
        padding: 8px 4px; border-bottom: 1px solid #eee; font-size: 0.9rem;
    }
    .freshness-label { font-weight: bold; }
    .freshness-value { font-size: 0.85rem; }
    .freshness-red { color: #c62828; font-weight: bold; }
    .freshness-ok { color: #2e7d32; }
    .freshness-info { color: #666; }

    /* NASの容量推移(不具合修正で新設)。使用率0-100%固定でスケールする折れ線。 */
    .nas-chart { width: 100%; max-width: 320px; height: auto; display: block; }
    .nas-chart-grid { stroke: #eee; stroke-width: 1; }
    .nas-chart-line { stroke: #1565c0; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
    details > summary {
        cursor: pointer; min-height: 44px; display: flex; align-items: center;
        font-size: 0.85rem; color: #1565c0; -webkit-tap-highlight-color: rgba(0,0,0,0.08);
    }

    .maintenance-box {
        border: 1px solid #eee; border-radius: 12px; padding: 12px; margin-bottom: 16px;
    }
    .maintenance-box button {
        min-height: 44px; border-radius: 10px; border: none; font-weight: bold;
        padding: 0 16px; margin-top: 8px;
    }
    button.danger { background: #c62828; color: #fff; }
    button.danger:disabled { background: #e0e0e0; color: #9e9e9e; }
    button.primary { background: #1565c0; color: #fff; }
    .maintenance-result { font-size: 0.85rem; margin-top: 8px; white-space: pre-wrap; }

    /* 見守りページのカメラ選択 */
    .camera-select-row { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
    .camera-btn {
        min-height: 44px; padding: 0 14px; border-radius: 12px; border: 1px solid #bbdefb;
        background: #e3f2fd; color: #1565c0; font-weight: bold; font-size: 0.9rem;
    }
    .camera-btn.active { background: #1565c0; color: #fff; }
    .camera-video-box {
        width: 100%; max-width: 480px; aspect-ratio: 16 / 9; background: #000;
        border-radius: 8px; overflow: hidden; margin-bottom: 16px;
    }
    .camera-video-box video { width: 100%; height: 100%; object-fit: contain; }

    @media (prefers-color-scheme: dark) {
        body { background: #121212; color: #e8e8e8; }
        .meta { color: #9e9e9e; }
        .alerts-warn { background: #3a2a14; color: #ffb74d; border-color: #5c4322; }
        .alerts-ok { background: #1e2a17; color: #aed581; border-color: #33482a; }
        nav.top-nav a, a.back-link { background: #16304a; color: #90caf9; border-color: #24507a; }
        a.nav-card { background: #23264a; color: #c5cae9; border-color: #34386b; }
        a.external-card { background: #0d2b28; color: #4db6ac; border-color: #14413c; }
        table.simple-table th { color: #9e9e9e; }
        table.simple-table th, table.simple-table td { border-color: #333; }
        .snapshot-caption { color: #9e9e9e; }
        .cost-date { color: #9e9e9e; }
        .cost-bar-track { background: #333; }
        .info-box { border-color: #333; }
        .freshness-row { border-color: #333; }
        .nas-chart-grid { stroke: #333; }
        .nas-chart-line { stroke: #64b5f6; }
        details > summary { color: #90caf9; }
        .maintenance-box { border-color: #333; }
        .camera-btn { background: #16304a; color: #90caf9; border-color: #24507a; }
        #status.stale::after { color: #ef9a9a; }
    }
"""


def _page_shell(title: str, body_html: str, *, extra_head: str = "") -> str:
    return (
        "<!DOCTYPE html>"
        '<html lang="ja"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title>"
        f"<style>{_PAGE_BASE_CSS}{home_status_service.STATUS_CARD_CSS}</style>"
        f"{extra_head}"
        "</head><body>"
        f"{body_html}"
        "</body></html>"
    )


def _back_to_home_link(dashboard_path: str) -> str:
    return f'<p><a class="back-link" href="{html.escape(dashboard_path)}">← ホームへ戻る</a></p>'


# === ホームページ ===

# 見守り/くらし/システムの3ページへの導線。カードの詳細タブ(`tab`)と同じキーを使う。
_NAV_CARDS: tuple[tuple[str, str, str], ...] = (
    ("watch", "👀 見守り", "カメラ・実家の様子"),
    ("life", "💡 くらし", "電気"),
    ("sys", "🔧 システム", "各機能の状態"),
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
) -> str:
    """ホームページ: ステータスカード + 外部リンク + 各ページへのナビ。

    外部リンク(ファミクエ・あさノート)は、以前はナビカードのさらに下、ページ最下部に
    警告表示(`.alerts-warn`)と紛らわしい配色で置かれており、「位置が分かりにくい」
    「色で気づきにくい」の両方の原因になっていた。ステータスカードのすぐ下・
    ナビカードより前に上げ、警告色と被らない配色(`.external-card`)にすることで
    両方を解消する。
    """
    nav_html = "".join(
        f'<a class="nav-card" href="{dashboard_path.rstrip("/")}/{key}">'
        f"{html.escape(label)}<span class=\"nav-card-sub\">{html.escape(sub)}</span></a>"
        for key, label, sub in _NAV_CARDS
    )
    links_html = (
        f'<a class="external-card" href="{html.escape(quest_path)}">⚔️ ファミクエ <span>›</span></a>'
        f'<a class="external-card" href="{html.escape(asa_note_url)}">📝 あさノート <span>›</span></a>'
    )
    body = (
        "<h1>🏠 おうちの様子</h1>"
        f'{_render_status_section(cards, fetched_at, dashboard_path=dashboard_path, refresh_sec=refresh_sec)}'
        '<h2>よく使うリンク</h2>'
        f'<div class="link-grid">{links_html}</div>'
        '<h2>メニュー</h2>'
        f'<div class="link-grid">{nav_html}</div>'
    )
    extra_head = _status_refresh_script(status_path, refresh_sec)
    if manifest_path is not None and icon_path is not None:
        extra_head = _home_screen_install_head(manifest_path, icon_path) + extra_head
    return _page_shell("おうちの様子", body, extra_head=extra_head)


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


def _render_status_section(cards, fetched_at: datetime, *, dashboard_path: str, refresh_sec: int) -> str:
    return (
        f'<div id="{STATUS_SECTION_ID}">'
        f'<p class="meta">{fetched_at.strftime("%m/%d %H:%M:%S")} 時点'
        f"・{int(refresh_sec)}秒ごとに自動更新</p>"
        f"{home_status_service.render_status_grid_html(cards, dashboard_path=dashboard_path)}"
        "</div>"
    )


def render_home_status_section(cards, fetched_at: datetime, *, dashboard_path: str, refresh_sec: int) -> str:
    """ホームページの自動更新用フラグメント(カードのブロックだけ)。"""
    return _render_status_section(cards, fetched_at, dashboard_path=dashboard_path, refresh_sec=refresh_sec)


def _status_refresh_script(status_path: str, refresh_sec: int) -> str:
    """カードのブロックだけを差し替える自動更新スクリプト(旧・軽量ページと同じ方式)。"""
    return (
        "<script>"
        "(function () {"
        f"var url = {json.dumps(status_path)};"
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


def _render_camera_selector(cameras: list[dict[str, Any]]) -> str:
    if not cameras:
        return '<p class="empty-note">カメラが登録されていません</p>'
    buttons = "".join(
        f'<button type="button" class="camera-btn" data-camera-id="{html.escape(cam["id"])}" '
        f'onclick="dashboardSelectCamera(\'{html.escape(cam["id"])}\')">{html.escape(cam["name"])}</button>'
        for cam in cameras
    )
    return (
        f'<div class="camera-select-row">{buttons}</div>'
        '<div class="camera-video-box"><video id="dashboardCameraVideo" muted autoplay playsinline controls></video></div>'
    )


_CAMERA_SCRIPT = """
<script src="https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js"></script>
<script>
(function () {
    var STORAGE_KEY = "dashboardSelectedCamera";
    var hls = null;

    window.dashboardSelectCamera = function (cameraId) {
        var video = document.getElementById("dashboardCameraVideo");
        if (!video) { return; }
        var url = "/api/cameras/live/" + encodeURIComponent(cameraId) + "/stream.m3u8";

        if (hls) { hls.destroy(); hls = null; }
        if (window.Hls && window.Hls.isSupported()) {
            hls = new window.Hls();
            hls.loadSource(url);
            hls.attachMedia(video);
            hls.on(window.Hls.Events.MANIFEST_PARSED, function () {
                video.play().catch(function () {});
            });
        } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
            video.src = url;
            video.addEventListener("loadedmetadata", function () {
                video.play().catch(function () {});
            });
        }

        try { localStorage.setItem(STORAGE_KEY, cameraId); } catch (e) {}
        document.querySelectorAll(".camera-btn").forEach(function (btn) {
            btn.classList.toggle("active", btn.dataset.cameraId === cameraId);
        });
    };

    document.addEventListener("DOMContentLoaded", function () {
        var buttons = document.querySelectorAll(".camera-btn");
        if (!buttons.length) { return; }
        var ids = Array.prototype.map.call(buttons, function (b) { return b.dataset.cameraId; });

        // ホームページの「🚗 駐車場」カードは、このページを開いたときに駐車場カメラを
        // 選択済みにしたい(?camera=<id>)。指定が無い/未知のidなら記憶(localStorage)、
        // それも無ければ先頭のカメラにフォールバックする。
        var requested = null;
        try { requested = new URLSearchParams(window.location.search).get("camera"); } catch (e) {}
        var saved = null;
        try { saved = localStorage.getItem(STORAGE_KEY); } catch (e) {}
        var initial = (requested && ids.indexOf(requested) !== -1) ? requested
            : (saved && ids.indexOf(saved) !== -1) ? saved : ids[0];
        window.dashboardSelectCamera(initial);
    });
})();
</script>
"""


def render_watch_page(
    df_sensor: pd.DataFrame,
    *,
    dashboard_path: str,
    snapshot_url_prefix: str,
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
    """
    cameras = [
        {"id": cam["id"], "name": cam["name"]}
        for cam in config.CAMERAS
        if cam.get("enabled", True)
    ]
    df_camera_motion = home_status_service.camera_motion_log(df_sensor)
    if not df_sensor.empty and "location" in df_sensor.columns:
        df_takasago = df_sensor[df_sensor["location"] == "高砂"]
        df_itami = df_sensor[df_sensor["location"] == "伊丹"]
    else:
        df_takasago = df_sensor.iloc[0:0]
        df_itami = df_sensor.iloc[0:0]

    body = (
        f"{_back_to_home_link(dashboard_path)}"
        "<h1>👀 見守り</h1>"
        '<div id="camera-section">'
        "<h2>🎥 カメラの映像</h2>"
        f"{_render_camera_selector(cameras)}"
        "</div>"
        "<h2>🖼️ 最近の写真</h2>"
        f"{_render_snapshot_gallery(snapshot_url_prefix)}"
        "<h2>🛡️ 防犯ログ</h2>"
        f'{_render_collapsible_log_table(df_camera_motion, {"timestamp": "検知時刻", "friendly_name": "カメラ"})}'
        '<div id="takasago-log">'
        "<h2>👵 高砂実家のセンサーログ</h2>"
        f'{_render_collapsible_log_table(df_takasago, {"timestamp": "時刻", "friendly_name": "センサー", "contact_state": "状態"})}'
        "</div>"
        '<div id="itami-log">'
        "<h2>🏠 伊丹(自宅)のセンサーログ</h2>"
        f'{_render_collapsible_log_table(df_itami, {"timestamp": "時刻", "friendly_name": "センサー", "movement_state": "動体", "contact_state": "開閉"})}'
        "</div>"
    )
    return _page_shell("見守り - おうちの様子", body, extra_head=_CAMERA_SCRIPT)


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
        f"{_back_to_home_link(dashboard_path)}"
        "<h1>💡 くらし</h1>"
        f"{home_status_service.render_status_grid_html(life_cards)}"
        '<p class="empty-note">今月の電気代はスマートメーターの記録からの概算です。</p>'
        "<h2>📊 日別の電気代(概算)</h2>"
        f"{_render_daily_cost_history(daily_cost_rows or [])}"
    )
    return _page_shell("くらし - おうちの様子", body)


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


# NASの容量推移グラフのサイズ(viewBox)。使用率(0-100%)固定でスケールするため
# 実測範囲での拡大縮小は行わない。
_NAS_CHART_WIDTH = 300
_NAS_CHART_HEIGHT = 90
_NAS_CHART_PAD = 8


def _render_nas_history_chart(df: pd.DataFrame) -> str:
    """NASの使用率推移を簡易な折れ線グラフ(インラインSVG)で表示する。

    **(不具合修正で新設)** NASカードをタップしても容量の履歴を見る手段が無かった。
    `analysis_service.load_nas_history`(`nas_records`、古い順)の`percent`列を
    折れ線で描画する。グラフだけでは正確な値を読み取れないため、`<details>`で
    折りたたんだ詳細テーブル(直近10件)も添える。
    """
    if df.empty or "percent" not in df.columns or len(df) < 2:
        return '<p class="empty-note">表示できるデータがありません</p>'

    values = df["percent"].astype(float)
    w, h, pad = _NAS_CHART_WIDTH, _NAS_CHART_HEIGHT, _NAS_CHART_PAD
    plot_w, plot_h = w - pad * 2, h - pad * 2
    step = plot_w / (len(values) - 1)

    def _y(v: float) -> float:
        return pad + plot_h - (max(0.0, min(100.0, v)) / 100.0) * plot_h

    points = " ".join(f"{pad + i * step:.1f},{_y(v):.1f}" for i, v in enumerate(values))
    grid = "".join(
        f'<line x1="{pad}" y1="{_y(v):.1f}" x2="{w - pad}" y2="{_y(v):.1f}" class="nas-chart-grid" />'
        for v in (0, 50, 100)
    )
    latest = values.iloc[-1]
    oldest_at = home_status_service.format_short_timestamp(df["timestamp"].iloc[0])
    latest_at = home_status_service.format_short_timestamp(df["timestamp"].iloc[-1])
    svg = (
        f'<svg viewBox="0 0 {w} {h}" class="nas-chart" role="img" '
        f'aria-label="NASの使用率推移。{html.escape(oldest_at)}から{html.escape(latest_at)}まで、'
        f'現在の使用率は{latest:.0f}パーセント">'
        f"{grid}"
        f'<polyline points="{points}" class="nas-chart-line" fill="none" />'
        "</svg>"
    )
    caption = f'<p class="meta">現在 {latest:.0f}%・{html.escape(oldest_at)} 〜 {html.escape(latest_at)}</p>'
    detail_table = _render_simple_table(
        df.tail(10).iloc[::-1], {"timestamp": "日時", "percent": "使用率(%)", "free_gb": "空き(GB)"}
    )
    return f"{svg}{caption}<details><summary>詳細データを見る</summary>{detail_table}</details>"


def render_sys_page(
    df_sensor: pd.DataFrame,
    nas_data: pd.Series | None,
    nas_history: pd.DataFrame,
    memory: dict[str, float] | None,
    disk: dict[str, float] | None,
    now: datetime,
    *,
    dashboard_path: str,
) -> str:
    """🔧 システム: 全体サマリー・各機能の最終更新時刻・NASの容量推移・メンテナンス操作。

    **(不具合修正)** 各セクションを`.info-box`で視覚的にグループ化してシステムページを
    見やすくした。NASカード(`id="nas-history"`)のタップ先として、NASの容量推移
    グラフ(`nas_history`)を追加した。
    """
    rows = build_freshness_rows(df_sensor, nas_data, memory, now)

    disk_line = ""
    if disk and "percent" in disk:
        disk_line = f'<p class="meta">保存容量の使用率: {int(disk["percent"])}%</p>'

    body = (
        f"{_back_to_home_link(dashboard_path)}"
        "<h1>🔧 システム</h1>"
        f"{_render_overall_summary(rows)}"
        '<div class="info-box">'
        "<h2>各機能の最終更新</h2>"
        f"{_render_freshness_rows(rows)}"
        f"{disk_line}"
        "</div>"
        '<div class="info-box" id="nas-history">'
        "<h2>🗄️ NASの容量推移</h2>"
        f"{_render_nas_history_chart(nas_history)}"
        "</div>"
        "<h2>🛠️ メンテナンス</h2>"
        '<div class="maintenance-box">'
        "<p>⚠️ 再起動するとダッシュボードやIoT機器の操作が一時的に使えなくなります。</p>"
        '<label><input type="checkbox" id="restartConfirm" onchange="'
        'document.getElementById(\'restartBtn\').disabled = !this.checked;">'
        "再起動することを理解しました</label><br>"
        '<button type="button" class="danger" id="restartBtn" disabled onclick="dashboardRestart()">'
        "🔄 システム再起動</button>"
        '<div id="restartResult" class="maintenance-result"></div>'
        "</div>"
        '<div class="maintenance-box">'
        "<p>データベースを今すぐバックアップします。</p>"
        '<button type="button" class="primary" onclick="dashboardBackup()">📦 今すぐバックアップ</button>'
        '<div id="backupResult" class="maintenance-result"></div>'
        "</div>"
    )
    return _page_shell("システム - おうちの様子", body, extra_head=_MAINTENANCE_SCRIPT)


_MAINTENANCE_SCRIPT = """
<script>
function dashboardPost(url, resultId, busyText) {
    var box = document.getElementById(resultId);
    box.textContent = busyText;
    fetch(url, { method: "POST", credentials: "same-origin" })
        .then(function (res) {
            return res.json().then(function (data) { return { ok: res.ok, data: data }; });
        })
        .then(function (result) {
            box.textContent = result.ok
                ? (result.data.message || "完了しました")
                : "失敗しました: " + (result.data.detail || "");
        })
        .catch(function (e) {
            box.textContent = "通信エラー: " + e;
        });
}
function dashboardRestart() { dashboardPost("/api/system/restart", "restartResult", "再起動コマンドを送信しています..."); }
function dashboardBackup() { dashboardPost("/api/system/backup", "backupResult", "バックアップ中..."); }
</script>
"""
