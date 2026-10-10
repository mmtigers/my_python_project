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
from datetime import date, datetime
from typing import Any

import config
import pandas as pd

from services import home_status_service, system_info_service

# === ページ共通のシェル ===

_PAGE_BASE_CSS = """
    /* === デザイントークン(色は「役割」で定義し、各ルールはここを参照する) ===
       ok=正常 / warn=注意 / bad=異常 / accent=操作できるもの・情報(青) / muted=補助文字。
       ダークモードはこの変数だけを差し替える。 */
    :root {
        color-scheme: light dark;
        --bg: #f3f5f9; --surface: #ffffff; --surface-2: #eef1f6; --border: #dfe4ec;
        --text: #1a2230; --muted: #5b6678; --faint: #8893a5;
        --accent: #2457d6; --accent-soft: #e8eefc; --accent-bd: #c5d3f5; --on-accent: #ffffff;
        --ok: #17713a; --ok-bg: #e6f5ec; --ok-bd: #b9e0c7;
        --warn: #8a5600; --warn-bg: #fff3d6; --warn-bd: #efd78e;
        --bad: #b3261e; --bad-bg: #fdebea; --bad-bd: #f2bdb9;
        --neutral: #5b6678; --neutral-bg: #f1f3f7; --neutral-bd: #dfe4ec;
        --bar-alt: #d9730d;
        --radius: 14px; --radius-sm: 10px;
        --shadow: 0 1px 2px rgba(20,30,50,0.06), 0 2px 8px rgba(20,30,50,0.04);
        --tap: rgba(36,87,214,0.12);
    }
    @media (prefers-color-scheme: dark) {
        :root {
            --bg: #0e1319; --surface: #171e28; --surface-2: #202938; --border: #2a3545;
            --text: #e6ebf2; --muted: #9aa7b8; --faint: #6f7d90;
            --accent: #8ab4ff; --accent-soft: #1a2a48; --accent-bd: #2d4577; --on-accent: #0e1319;
            --ok: #7fd39a; --ok-bg: #112a1c; --ok-bd: #22533a;
            --warn: #f2c066; --warn-bg: #30250d; --warn-bd: #574314;
            --bad: #ff9d96; --bad-bg: #351614; --bad-bd: #692a26;
            --neutral: #9aa7b8; --neutral-bg: #1c2431; --neutral-bd: #2a3545;
            --bar-alt: #f2a54a;
            --shadow: 0 1px 2px rgba(0,0,0,0.4);
            --tap: rgba(138,180,255,0.16);
        }
    }
    * { box-sizing: border-box; }
    body {
        margin: 0 auto;
        max-width: 1080px;
        padding: 16px 16px 40px;
        background: var(--bg);
        color: var(--text);
        font-family: "Helvetica Neue", Arial, "Hiragino Kaku Gothic ProN", "Hiragino Sans", Meiryo, sans-serif;
        line-height: 1.5;
        -webkit-text-size-adjust: 100%;
    }
    h1 { font-size: 1.4rem; font-weight: 800; letter-spacing: 0.01em; margin: 4px 0 2px; }
    h2 {
        font-size: 0.95rem; font-weight: 700; margin: 24px 0 10px; color: var(--text);
    }
    .meta { font-size: 0.8rem; color: var(--muted); margin: 0 0 14px; }

    .alerts {
        margin: 0 0 10px; padding: 10px 12px; border-radius: var(--radius-sm);
        font-size: 0.85rem; line-height: 1.5;
    }
    .alerts-warn { background: var(--warn-bg); color: var(--warn); border: 1px solid var(--warn-bd); }
    .alerts-ok { background: var(--ok-bg); color: var(--ok); border: 1px solid var(--ok-bd); }
    .alerts a { color: inherit; font-weight: bold; display: inline-flex; align-items: center; min-height: 44px; }

    /* 自動更新が失敗したときの見た目(取れなかったら古い表示を消さずに薄く残す)。 */
    #status.stale { opacity: 0.55; }
    #status.stale::after {
        content: "⚠️ 更新できていません(表示は最後に取得できた内容です)";
        display: block; margin-top: 8px; font-size: 0.8rem; color: var(--bad);
    }

    /* 操作できる要素(リンク・ボタン)は共通で「青の面+枠+太字」に揃え、
       表示だけの要素(カード・箱)は白地+細い枠で区別する。 */
    nav.top-nav { margin: 0 0 16px; }
    nav.top-nav a, a.back-link {
        display: inline-flex; align-items: center; min-height: 44px;
        padding: 0 16px; border-radius: 999px; border: 1px solid var(--accent-bd);
        background: var(--accent-soft); color: var(--accent); font-weight: bold;
        text-decoration: none; font-size: 0.9rem;
        -webkit-tap-highlight-color: var(--tap);
    }
    nav.top-nav a:hover, a.back-link:hover { border-color: var(--accent); }
    a:focus-visible, button:focus-visible, summary:focus-visible, select:focus-visible, input:focus-visible {
        outline: 2px solid var(--accent); outline-offset: 2px;
    }

    /* ホーム画面のナビカードと外部リンクカード。「タップで移動する」ことが分かるよう
       右端に矢印(›)を添える。 */
    .link-grid {
        display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
        gap: 10px; margin: 8px 0 4px;
    }
    a.nav-card {
        position: relative; display: flex; flex-direction: column; justify-content: center; align-items: flex-start;
        gap: 2px; padding: 14px 32px 14px 16px; min-height: 76px; border-radius: var(--radius);
        text-decoration: none; font-weight: bold; font-size: 1rem;
        background: var(--surface); color: var(--text); border: 1px solid var(--border);
        box-shadow: var(--shadow); -webkit-tap-highlight-color: var(--tap);
        transition: border-color 0.15s, background-color 0.15s;
    }
    a.nav-card::after {
        content: "›"; position: absolute; right: 14px; top: 50%; transform: translateY(-50%);
        font-size: 1.5rem; font-weight: normal; color: var(--accent);
    }
    a.nav-card:hover { border-color: var(--accent); background: var(--accent-soft); }
    a.nav-card:active { transform: scale(0.98); }
    a.nav-card .nav-card-sub { font-size: 0.75rem; font-weight: normal; color: var(--muted); }
    /* 外部リンク(ファミクエ等)。稼働状況は色+文字(.link-state)の両方で示す。
       既定(状態不明)は警告色と被らない青系。 */
    a.external-card {
        display: flex; align-items: center; justify-content: space-between;
        gap: 8px; padding: 14px 16px; min-height: 52px; border-radius: var(--radius);
        text-decoration: none; font-weight: bold; font-size: 0.95rem;
        background: var(--accent-soft); color: var(--accent); border: 1px solid var(--accent-bd);
        -webkit-tap-highlight-color: var(--tap);
    }
    a.external-card:hover { border-color: var(--accent); }
    a.external-card.external-up { background: var(--ok-bg); color: var(--ok); border-color: var(--ok-bd); }
    a.external-card.external-down { background: var(--bad-bg); color: var(--bad); border-color: var(--bad-bd); }
    .link-state { font-size: 0.75rem; font-weight: normal; margin-left: 4px; }

    /* 見守りページの簡易テーブル(防犯ログ・実家センサーログ)。 */
    table.simple-table {
        width: 100%; border-collapse: collapse; font-size: 0.85rem; margin-bottom: 16px;
        background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm);
    }
    table.simple-table th, table.simple-table td {
        text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border);
    }
    table.simple-table tr:last-child td { border-bottom: none; }
    table.simple-table th {
        color: var(--muted); font-weight: bold; font-size: 0.75rem; background: var(--surface-2);
    }
    /* システムページのバックアップ完了/失敗トースト(画面下部に数秒だけ出る) */
    .dashboard-toast {
        position: fixed; left: 16px; right: 16px; bottom: 24px; z-index: 1000;
        max-width: 480px; margin: 0 auto;
        padding: 14px 16px; border-radius: var(--radius); font-weight: bold; font-size: 0.95rem;
        color: #fff; box-shadow: 0 6px 20px rgba(0,0,0,0.3); transition: opacity 0.5s;
    }
    .dashboard-toast.ok { background: #1f7a3d; }
    .dashboard-toast.ng { background: #b3261e; }
    .dashboard-toast.hide { opacity: 0; }
    .empty-note {
        color: var(--muted); font-size: 0.85rem; margin: 4px 0 16px; padding: 12px;
        border: 1px dashed var(--border); border-radius: var(--radius-sm);
    }

    /* カメラのスナップショットギャラリー(`.snapshot-item`が`<a target="_blank">`)。 */
    .snapshot-grid {
        display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
        gap: 8px; margin-bottom: 16px;
    }
    a.snapshot-item {
        display: block; text-decoration: none; color: inherit;
        -webkit-tap-highlight-color: var(--tap);
    }
    .snapshot-item img {
        width: 100%; border-radius: var(--radius-sm); display: block; background: var(--surface-2);
        border: 1px solid var(--border);
    }
    .snapshot-caption {
        display: block; margin-top: 4px; font-size: 0.7rem; color: var(--muted); text-align: center;
    }

    /* くらしページの電気代詳細(日別推移の簡易バーグラフ) */
    .cost-history {
        margin-bottom: 16px; padding: 8px 14px; background: var(--surface);
        border: 1px solid var(--border); border-radius: var(--radius);
    }
    .cost-row {
        display: flex; align-items: center; gap: 10px; padding: 5px 0; font-size: 0.85rem;
    }
    .cost-date { flex: none; width: 3.4em; color: var(--muted); }
    .cost-bar-track {
        flex: 1; height: 10px; border-radius: 5px; background: var(--surface-2); overflow: hidden;
    }
    .cost-bar { height: 100%; border-radius: 5px; background: var(--accent); }
    .cost-value { flex: none; width: 4.6em; text-align: right; font-weight: bold; }

    /* システムページ等の各セクションを視覚的にグループ化する箱 */
    .info-box {
        background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
        padding: 14px; margin-bottom: 16px; box-shadow: var(--shadow);
    }
    .info-box > h2:first-child { margin-top: 0; }

    /* システムページの機能ごとの鮮度一覧 */
    .freshness-row {
        display: flex; justify-content: space-between; align-items: baseline; gap: 12px;
        padding: 10px 4px; border-bottom: 1px solid var(--border); font-size: 0.9rem;
    }
    .freshness-row:last-child { border-bottom: none; }
    .freshness-label { font-weight: bold; }
    .freshness-value { font-size: 0.85rem; }
    .freshness-red { color: var(--bad); font-weight: bold; }
    .freshness-ok { color: var(--ok); }
    .freshness-info { color: var(--muted); }

    /* NASの容量推移。使用率0-100%固定でスケールする折れ線。 */
    .nas-chart { width: 100%; max-width: 420px; height: auto; display: block; }
    .nas-chart-grid { stroke: var(--border); stroke-width: 1; }
    .nas-chart-label { font-size: 8px; fill: var(--muted); }
    /* 電気ページ(月ごとの推移・日別・気温・テレビ。グラフはインラインSVG) */
    .bar-chart { width: 100%; max-width: 480px; height: auto; display: block; margin: 8px 0; }
    .bar-axis { stroke: var(--faint); stroke-width: 1; }
    .bar { fill: var(--accent); }
    .bar-alt { fill: var(--bar-alt); }
    .bar-faded { fill: var(--accent-bd); }
    .bar-label { font-size: 8px; fill: var(--muted); }
    .legend-box { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: middle; }
    .legend-box.bar { background: var(--accent); }
    .legend-box.bar-alt { background: var(--bar-alt); }
    .power-lead { font-size: 1.05rem; margin: 6px 0; }
    .power-stats { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin: 10px 0; }
    .power-stat {
        background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm);
        padding: 10px 12px; box-shadow: var(--shadow);
    }
    .power-stat-value { font-weight: bold; font-size: 1.1rem; word-break: break-word; }
    .band-days th, .band-days td { text-align: center; font-size: 0.7rem; padding: 2px; }
    /* アップデートページ(全アプリの更新履歴) */
    .release-filter { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0 12px; }
    .release-filter a {
        display: inline-flex; align-items: center; min-height: 44px; padding: 0 16px; border-radius: 22px;
        border: 1px solid var(--accent-bd); background: var(--accent-soft); color: var(--accent);
        text-decoration: none; font-size: 0.9rem;
    }
    .release-filter a.active { background: var(--accent); color: var(--on-accent); border-color: var(--accent); font-weight: bold; }
    .release-card {
        background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
        padding: 12px 14px; margin: 10px 0; box-shadow: var(--shadow);
    }
    .release-head { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; font-size: 0.85rem; }
    .release-badge { padding: 2px 10px; border-radius: 10px; background: #2457d6; color: #fff; font-weight: bold; }
    .release-badge-quest { background: #7b2cbf; }
    .release-badge-asa { background: #c2570c; }
    .release-badge-yoru { background: #34408f; }
    .release-title { font-weight: bold; margin: 6px 0 2px; word-break: break-word; }
    .release-card p, .release-card ul { margin: 4px 0; word-break: break-word; }
    .release-card ul { padding-left: 1.2em; }
    .release-warn { color: var(--warn); font-size: 0.9rem; }
    .git-status-line { margin: 4px 0; word-break: break-word; }
    .git-status-warn { color: var(--warn); }
    .git-commit-list { list-style: none; padding: 0; margin: 8px 0 0; }
    .git-commit-list li { padding: 8px 0; border-top: 1px solid var(--border); word-break: break-word; }
    .git-commit-sha { font-family: ui-monospace, Menlo, Consolas, monospace; }
    .git-commit-badge { font-size: 0.8rem; margin-left: 6px; white-space: nowrap; display: inline-block; }
    .nas-period a.nas-period-link {
        padding: 6px 12px; border-radius: 999px; background: var(--accent-soft); color: var(--accent);
        border: 1px solid var(--accent-bd); text-decoration: none;
    }
    .nas-period .nas-period-current {
        padding: 6px 12px; border-radius: 999px; background: var(--accent); color: var(--on-accent); font-weight: bold;
    }
    .nas-chart-line { stroke: var(--accent); stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
    details > summary {
        cursor: pointer; min-height: 44px; display: flex; align-items: center;
        font-size: 0.85rem; color: var(--accent); font-weight: bold; -webkit-tap-highlight-color: var(--tap);
    }

    .maintenance-box {
        background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
        padding: 14px; margin-bottom: 16px; box-shadow: var(--shadow);
    }
    .maintenance-box button {
        min-height: 44px; border-radius: var(--radius-sm); border: none; font-weight: bold;
        padding: 0 18px; margin-top: 8px; cursor: pointer; font-size: 0.9rem;
    }
    button.danger { background: var(--bad); color: #fff; }
    button.danger:disabled { background: var(--surface-2); color: var(--faint); cursor: not-allowed; }
    button.primary { background: var(--accent); color: var(--on-accent); }
    .maintenance-result { font-size: 0.85rem; margin-top: 8px; white-space: pre-wrap; }

    /* 見守りページのログの日付フィルタ */
    .log-filter { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 8px 0 8px; }
    .log-filter input[type="date"], .log-filter select {
        min-height: 44px; max-width: 100%; padding: 0 12px; border-radius: var(--radius-sm);
        border: 1px solid var(--border); font-size: 1rem; background: var(--surface); color: var(--text);
    }
    .log-filter button, a.log-filter-clear {
        min-height: 44px; padding: 0 18px; border-radius: var(--radius-sm); font-weight: bold; font-size: 0.9rem;
        display: inline-flex; align-items: center; box-sizing: border-box; cursor: pointer;
    }
    .log-filter button { border: none; background: var(--accent); color: var(--on-accent); }
    a.log-filter-clear { border: 1px solid var(--accent-bd); background: var(--accent-soft); color: var(--accent); text-decoration: none; }

    /* 見守りページのカメラ映像(全台を同時表示) */
    .camera-grid {
        display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 300px), 1fr));
        gap: 14px; margin-bottom: 16px;
    }
    .camera-name { font-weight: bold; font-size: 0.9rem; margin-bottom: 6px; }
    .camera-tile-requested .camera-name { color: var(--accent); }
    .camera-video-box {
        width: 100%; aspect-ratio: 16 / 9; background: #000;
        border-radius: var(--radius-sm); overflow: hidden; border: 1px solid var(--border);
    }
    .camera-video-box video { width: 100%; height: 100%; object-fit: contain; }
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
) -> str:
    """ホームページ: ステータスカード + 外部リンク + 各ページへのナビ。

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
    links_html = _render_external_links(quest_path, asa_note_url, link_health, yoru_note_url)
    body = (
        "<h1>🏠 おうちの様子</h1>"
        f'{_render_status_section(cards, fetched_at, dashboard_path=dashboard_path, refresh_sec=refresh_sec, extra_html=links_html)}'
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


def _render_status_section(
    cards, fetched_at: datetime, *, dashboard_path: str, refresh_sec: int, extra_html: str = ""
) -> str:
    return (
        f'<div id="{STATUS_SECTION_ID}">'
        f'<p class="meta">{fetched_at.strftime("%m/%d %H:%M:%S")} 時点'
        f"・{int(refresh_sec)}秒ごとに自動更新</p>"
        f"{home_status_service.render_status_grid_html(cards, dashboard_path=dashboard_path)}"
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
) -> str:
    """ホームページの自動更新用フラグメント(カードと、外部リンクのブロック)。

    `quest_path`/`asa_note_url`を渡したときだけ外部リンクを含める(ホームページ本体が
    リンクを自動更新の範囲の中に持つため、差し替え後もリンクが消えないようにする)。
    """
    links_html = (
        _render_external_links(quest_path, asa_note_url, link_health, yoru_note_url)
        if quest_path is not None and asa_note_url is not None
        else ""
    )
    return _render_status_section(
        cards, fetched_at, dashboard_path=dashboard_path, refresh_sec=refresh_sec, extra_html=links_html
    )


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

    各タイルは名前ラベル付きの`<video data-camera-id>`で、`_CAMERA_SCRIPT`が読み込み時に
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


_CAMERA_SCRIPT = """
<script src="https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js"></script>
<script>
(function () {
    function startStream(video) {
        var url = "/api/cameras/live/" + encodeURIComponent(video.dataset.cameraId) + "/stream.m3u8";
        if (window.Hls && window.Hls.isSupported()) {
            var hls = new window.Hls();
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
    }

    document.addEventListener("DOMContentLoaded", function () {
        var videos = document.querySelectorAll(".camera-video");
        if (!videos.length) { return; }

        // ホームページの「🚗 駐車場」カードは(?camera=<id>)でこのページを開く。
        // 全台を同時に表示するので、指定されたカメラのタイルを先頭に移して目立たせる。
        var requested = null;
        try { requested = new URLSearchParams(window.location.search).get("camera"); } catch (e) {}
        Array.prototype.forEach.call(videos, function (video) {
            if (requested && video.dataset.cameraId === requested) {
                var tile = video.closest(".camera-tile");
                if (tile && tile.parentNode) {
                    tile.classList.add("camera-tile-requested");
                    tile.parentNode.insertBefore(tile, tile.parentNode.firstChild);
                }
            }
        });
        // 先頭へ移したあとの並びで全台のストリームを開始する。
        document.querySelectorAll(".camera-video").forEach(startStream);
    });
})();
</script>
"""


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


def render_watch_page(
    df_sensor: pd.DataFrame,
    *,
    dashboard_path: str,
    snapshot_url_prefix: str,
    selected_date: date | None = None,
    selected_device: str | None = None,
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

    **(UI改善)** 高砂・伊丹のセンサーログは`sensor_state_change_log`で、開閉/動体
    センサーの「状態が変わった行」だけに絞る(温湿度計・電力プラグの5分おきの記録は
    ここには出さない。値の確認は各カード/システムページ側にある)。

    **(UI改善)** 開閉センサーの「open」の直後(60秒以内)に「close」が来た組は、
    `merge_open_close_events`で「開 → 閉（N秒）」の1行にまとめる。

    **(新機能)** `selected_device`(`device_id`)を指定すると、高砂・伊丹のセンサーログをその機器だけに絞る
    (防犯ログは対象外)。選択肢にない値は無視する。

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

    def _log_table(df: pd.DataFrame, columns: dict[str, str]) -> str:
        # 日付指定時は上限を外して全件(`limit`は`df.iloc[visible:limit]`の上端)。
        if selected_date is None:
            return _render_collapsible_log_table(df, columns)
        return _render_collapsible_log_table(df, columns, limit=max(len(df), _LOG_TABLE_VISIBLE_ROWS))

    body = (
        f"{_back_to_home_link(dashboard_path)}"
        "<h1>👀 見守り</h1>"
        '<div id="camera-section">'
        "<h2>🎥 カメラの映像</h2>"
        f"{_render_camera_selector(cameras)}"
        "</div>"
        "<h2>🖼️ 最近の写真</h2>"
        f"{_render_snapshot_gallery(snapshot_url_prefix)}"
        f"{_render_log_date_filter(dashboard_path, selected_date, device_options, selected_device)}"
        "<h2>🛡️ 防犯ログ</h2>"
        f'{_log_table(df_camera_motion, {"timestamp": "検知時刻", "friendly_name": "カメラ"})}'
        '<div id="takasago-log">'
        "<h2>👵 高砂実家のセンサーログ</h2>"
        f'{_log_table(df_takasago, {"timestamp": "時刻", "friendly_name": "センサー", "contact_state": "状態"})}'
        "</div>"
        '<div id="itami-log">'
        "<h2>🏠 伊丹(自宅)のセンサーログ</h2>"
        f'{_log_table(df_itami, {"timestamp": "時刻", "friendly_name": "センサー", "movement_state": "動体", "contact_state": "開閉"})}'
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
        f'<div class="link-grid"><a class="nav-card" href="{html.escape(dashboard_path.rstrip("/"))}/power">'
        '⚡ 電気のくわしい分析<span class="nav-card-sub">月ごとの推移・気温との関係・テレビ</span></a></div>'
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
        f"{_back_to_home_link(dashboard_path)}"
        "<h1>📜 アップデート</h1>"
        '<p class="meta">ダッシュボード・ファミクエ・あさノート・よるノートの更新履歴を、新しい順にまとめています。</p>'
        f'<div class="release-filter">{"".join(chips)}</div>'
        f"{warnings}"
        f"{cards}"
    )
    return _page_shell("アップデート - おうちの様子", body)


def _format_boot_time(value: Any) -> str:
    """起動履歴の時刻(ISO8601)を「10/09 21:05」形式にする。読めない値は空文字。"""
    return home_status_service.format_short_timestamp(value)


def _render_boot_history_box(boots: list[dict[str, Any]]) -> str:
    """サーバーの起動履歴(新しい順)。`system_info_service.get_boot_history`の結果を受け取る。

    起動の記録は`unified_server`の起動時に1行ずつ追記される。異常終了は記録できないため、
    「起動した時刻」の履歴であることを注記する。"""
    if not boots:
        body = '<p class="empty-note">起動の記録はまだありません(この機能の導入後の最初の起動から記録されます)</p>'
    else:
        rows = []
        for b in boots:
            sha = b.get("commit_sha") or "不明"
            subject = b.get("commit_subject") or ""
            reason = system_info_service.REASON_LABELS.get(b.get("reason") or "", "不明")
            rows.append(
                "<tr>"
                f"<td>{html.escape(_format_boot_time(b.get('booted_at')))}</td>"
                f"<td><span class=\"git-commit-sha\">{html.escape(str(sha))}</span> {html.escape(subject)}</td>"
                f"<td>{html.escape(reason)}</td>"
                "</tr>"
            )
        body = (
            '<table class="simple-table"><thead><tr><th>起動</th><th>コミット</th><th>きっかけ</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>'
            '<p class="meta">起動した時刻の履歴です(停電などの異常終了そのものは記録されません)。</p>'
        )
    return f'<div class="info-box" id="boot-history"><h2>🔁 サーバーの起動履歴</h2>{body}</div>'


def _render_git_status_box() -> str:
    """Gitの状態の枠。中身はページ表示後に`_GIT_STATUS_SCRIPT`が`/api/system/git/status`から埋める
    (`git fetch`を含み遅いので、ページ本体では待たない)。"""
    return (
        '<div class="info-box" id="git-status"><h2>🔀 Gitの状態</h2>'
        '<div id="gitStatusBody"><p class="meta">確認中...</p></div></div>'
    )


_GIT_STATUS_SCRIPT = """
<script>
(function () {
    function el(tag, text, cls) {
        var node = document.createElement(tag);
        if (cls) { node.className = cls; }
        node.textContent = text;
        return node;
    }
    function fmtBoot(iso) {
        return iso ? iso.slice(5, 10).replace("-", "/") + " " + iso.slice(11, 16) : "";
    }
    function commitText(c) { return c.sha + " " + c.subject + (c.date ? " (" + c.date + ")" : ""); }

    function render(d) {
        var box = document.getElementById("gitStatusBody");
        if (!box) { return; }
        box.textContent = "";
        if (!d.ok) { box.appendChild(el("p", d.error || "取得できませんでした", "git-status-line git-status-warn")); return; }
        if (d.running) {
            box.appendChild(el("p", "▶ 起動中のコード: " + d.running.sha + " " + d.running.subject
                + "(起動 " + fmtBoot(d.running.booted_at) + ")", "git-status-line"));
        } else {
            box.appendChild(el("p", "▶ 起動中のコード: 記録なし(この機能の導入後の最初の起動から分かります)", "git-status-line"));
        }
        box.appendChild(el("p", "📁 手元(実機)の最新: " + commitText(d.head) + (d.branch ? " ・ ブランチ " + d.branch : ""), "git-status-line"));
        if (d.pending_restart) {
            box.appendChild(el("p", "⚠️ 取り込み済みですが、まだ再起動されていません(起動中のコードは古いままです)", "git-status-line git-status-warn"));
        }
        if (d.remote) {
            box.appendChild(el("p", "☁️ GitHubの最新: " + commitText(d.remote), "git-status-line"));
            if (d.behind === 0) {
                box.appendChild(el("p", "✅ 手元はGitHubの最新に追いついています", "git-status-line"));
            } else if (d.behind > 0) {
                box.appendChild(el("p", "⬇️ GitHubの最新まで、あと" + d.behind + "件取り込まれていません", "git-status-line git-status-warn"));
            }
            if (d.ahead > 0) {
                box.appendChild(el("p", "手元にだけあるコミットが" + d.ahead + "件あります", "git-status-line git-status-warn"));
            }
        }
        if (d.error) { box.appendChild(el("p", d.error, "git-status-line git-status-warn")); }
        if (d.recent && d.recent.length) {
            box.appendChild(el("p", "最近のコミット", "meta"));
            var list = document.createElement("ul");
            list.className = "git-commit-list";
            d.recent.forEach(function (c) {
                var li = document.createElement("li");
                li.appendChild(el("span", c.sha, "git-commit-sha"));
                li.appendChild(document.createTextNode(" " + c.subject + (c.date ? " (" + c.date + ")" : "")));
                var badge = c.running ? "▶ 起動中" : (c.pulled ? "✅ 取り込み済み" : "⬇️ 未取り込み");
                li.appendChild(el("span", badge, "git-commit-badge"));
                list.appendChild(li);
            });
            box.appendChild(list);
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        fetch("/api/system/git/status", { credentials: "same-origin" })
            .then(function (res) { return res.json(); })
            .then(render)
            .catch(function () {
                var box = document.getElementById("gitStatusBody");
                if (box) { box.textContent = "Gitの状態を取得できませんでした"; }
            });
    });
})();
</script>
"""


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
        f"{_render_nas_history_chart(nas_history, days=nas_days, sys_path=dashboard_path.rstrip('/') + '/sys')}"
        "</div>"
        f"{_render_boot_history_box(boot_history or [])}"
        f"{_render_git_status_box()}"
        "<h2>🛠️ メンテナンス</h2>"
        '<div class="maintenance-box">'
        "<p>GitHubの最新を取り込んで、システムを再起動します。"
        "取り込みに失敗した場合は再起動しません(新しい内容が無いときも再起動しません)。</p>"
        "<p>⚠️ 再起動中はダッシュボードやIoT機器の操作が一時的に使えなくなります。数分かかることがあります。</p>"
        '<label><input type="checkbox" id="deployConfirm" onchange="'
        'document.getElementById(\'deployBtn\').disabled = !this.checked;">'
        "最新に更新して再起動することを理解しました</label><br>"
        '<button type="button" class="danger" id="deployBtn" disabled onclick="dashboardDeploy()">'
        "⬇️ 最新に更新して再起動</button>"
        '<div id="deployResult" class="maintenance-result" role="status" aria-live="polite"></div>'
        "</div>"
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
        '<p class="meta" id="backupLatest">最新のバックアップ: 確認中...</p>'
        '<button type="button" class="primary" id="backupBtn" onclick="dashboardBackup()">'
        "📦 今すぐバックアップ</button>"
        '<div id="backupResult" class="maintenance-result" role="status" aria-live="polite"></div>'
        "</div>"
    )
    return _page_shell("システム - おうちの様子", body, extra_head=_MAINTENANCE_SCRIPT + _GIT_STATUS_SCRIPT)


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

var backupPollTimer = null;
function dashboardToast(text, ok) {
    var el = document.createElement("div");
    el.className = "dashboard-toast " + (ok ? "ok" : "ng");
    el.setAttribute("role", "alert");
    el.textContent = text;
    document.body.appendChild(el);
    setTimeout(function () { el.classList.add("hide"); }, 5000);
    setTimeout(function () { if (el.parentNode) { el.parentNode.removeChild(el); } }, 5600);
}
function backupFormatTime(iso) {
    var m = /^(\\d{4})-(\\d{2})-(\\d{2})T(\\d{2}):(\\d{2})/.exec(iso || "");
    return m ? (m[2] + "/" + m[3] + " " + m[4] + ":" + m[5]) : "";
}
function backupSetBusy(busy) {
    var btn = document.getElementById("backupBtn");
    btn.disabled = busy;
    btn.textContent = busy ? "⏳ バックアップ中..." : "📦 今すぐバックアップ";
}
function backupShowLatest(info) {
    var box = document.getElementById("backupLatest");
    if (!info) { box.textContent = "最新のバックアップ: 見つかりません"; return; }
    box.textContent = "最新のバックアップ: " + backupFormatTime(info.created_at) + " (" + info.size_mb + "MB)";
}
function backupFetchStatus() {
    return fetch("/api/system/backup/status", { credentials: "same-origin" })
        .then(function (res) { if (!res.ok) { throw new Error("HTTP " + res.status); } return res.json(); });
}
function backupPoll(runId) {
    backupFetchStatus().then(function (st) {
        backupShowLatest(st.latest_backup);
        if (st.running) { backupPollTimer = setTimeout(function () { backupPoll(runId); }, 2000); return; }
        backupSetBusy(false);
        var box = document.getElementById("backupResult");
        var last = st.last_result;
        if (last && (runId === null || last.run_id === runId)) {
            var ok = last.success;
            var text = ok ? "✅ バックアップが完了しました (" + last.size_mb + "MB)" : "❌ バックアップに失敗しました: " + last.message;
            box.textContent = text;
            dashboardToast(text, ok);
        } else {
            box.textContent = "";
        }
    }).catch(function (e) {
        // 一時的な通信エラーでは諦めず、少し待って再確認する(結果はサーバー側に残る)。
        backupPollTimer = setTimeout(function () { backupPoll(runId); }, 4000);
    });
}
function dashboardBackup() {
    var box = document.getElementById("backupResult");
    backupSetBusy(true);
    box.textContent = "バックアップを開始しています...";
    fetch("/api/system/backup", { method: "POST", credentials: "same-origin" })
        .then(function (res) { return res.json().then(function (d) { return { ok: res.ok, data: d }; }); })
        .then(function (r) {
            if (!r.ok) { throw new Error(r.data.detail || "開始できませんでした"); }
            box.textContent = "バックアップ中です。完了までお待ちください...";
            backupPoll(r.data.run_id);
        })
        .catch(function (e) {
            backupSetBusy(false);
            box.textContent = "❌ " + e.message;
            dashboardToast("❌ バックアップを開始できませんでした: " + e.message, false);
        });
}
var deployFailCount = 0;
function deploySetBusy(busy) {
    var btn = document.getElementById("deployBtn");
    var chk = document.getElementById("deployConfirm");
    if (busy) {
        btn.disabled = true;
        btn.textContent = "⏳ 更新中...";
    } else {
        btn.textContent = "⬇️ 最新に更新して再起動";
        btn.disabled = !chk.checked;
    }
}
function deployPoll(runId) {
    var box = document.getElementById("deployResult");
    fetch("/api/system/deploy/status", { credentials: "same-origin" })
        .then(function (res) { if (!res.ok) { throw new Error("HTTP " + res.status); } return res.json(); })
        .then(function (st) {
            deployFailCount = 0;
            if (st.running) {
                box.textContent = "更新中です。完了までお待ちください...";
                setTimeout(function () { deployPoll(runId); }, 3000);
                return;
            }
            deploySetBusy(false);
            var last = st.last_result;
            if (last && (runId === null || last.run_id === runId)) {
                var text = (last.success ? "✅ " : "❌ ") + last.message;
                box.textContent = last.success ? text + " ページを再読み込みすると新しい画面になります。" : text;
                dashboardToast(text, last.success);
            } else {
                box.textContent = "";
            }
        })
        .catch(function () {
            // 再起動中はサーバーが応答しない。諦めず待つ(結果は状態ファイルに残る)。
            deployFailCount += 1;
            if (deployFailCount > 200) {
                deploySetBusy(false);
                box.textContent = "❌ サーバーが長時間応答しません。実機の状態を確認してください。";
                return;
            }
            box.textContent = "サーバーを再起動しています。しばらくお待ちください...";
            setTimeout(function () { deployPoll(runId); }, 4000);
        });
}
function dashboardDeploy() {
    var box = document.getElementById("deployResult");
    deploySetBusy(true);
    box.textContent = "更新を開始しています...";
    fetch("/api/system/deploy", { method: "POST", credentials: "same-origin" })
        .then(function (res) { return res.json().then(function (d) { return { ok: res.ok, data: d }; }); })
        .then(function (r) {
            if (!r.ok) { throw new Error(r.data.detail || "開始できませんでした"); }
            box.textContent = "更新中です。完了までお待ちください...";
            deployPoll(r.data.run_id);
        })
        .catch(function (e) {
            deploySetBusy(false);
            box.textContent = "❌ " + e.message;
            dashboardToast("❌ 更新を開始できませんでした: " + e.message, false);
        });
}
document.addEventListener("DOMContentLoaded", function () {
    // 更新の実行中にページを開き直した場合は、結果を待つ表示に戻す。
    fetch("/api/system/deploy/status", { credentials: "same-origin" })
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (st) { if (st && st.running) { deploySetBusy(true); deployPoll(null); } })
        .catch(function () {});
    // ページを開いた時点で実行中なら、スピナー表示に戻して結果を待つ。
    backupFetchStatus().then(function (st) {
        backupShowLatest(st.latest_backup);
        if (st.running) { backupSetBusy(true); backupPoll(null); }
    }).catch(function () {
        document.getElementById("backupLatest").textContent = "最新のバックアップ: 取得できませんでした";
    });
});
</script>
"""
