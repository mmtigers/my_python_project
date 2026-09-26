# MY_HOME_SYSTEM/tests/test_dashboard_mobile_e2e.py
"""スマートフォン幅の実画面に対する回帰テスト(Playwright)。

なぜ必要か:
    2026-09 の #807 / #822 は、いずれも「実機のスマホで開いて初めて分かった」
    崩れだった(サマリーカードが生HTMLとして出る、固定ヘッダーが半透明で本文が
    透ける、先頭の行がヘッダーの下に隠れる)。既存の回帰テストはCSS文字列や
    描画関数の呼び出しを見るもので、**実際に描かれた結果の座標**は見ていない。
    そのため「CSSは入っているのにレイアウトは崩れている」を拾えない。

    #829でStreamlit版の詳細表示は廃止し、ダッシュボードは
    `routers/dashboard_router.py`(FastAPIがHTMLを直接返す)だけになった。
    ここでは390x844(iPhone相当)のビューポートで実際に開いて、
    レイアウトの不変条件を検証する。

実行方法:
    ブラウザのダウンロードを伴うため、既定ではスキップする。

        DASHBOARD_E2E=1 python -m pytest tests/test_dashboard_mobile_e2e.py -v

    CI では非ブロッキングの専用ワークフロー
    (.github/workflows/dashboard-mobile-e2e.yml)から同じ形で実行する。
    `playwright install chromium` を済ませていない環境(ラズパイ実機や、
    別ビルドのブラウザが用意済みのコンテナ)では、`PLAYWRIGHT_CHROMIUM_PATH` に
    Chromium/Chrome の実行ファイルを指定すればそれを使う。
    スクリーンショットは `DASHBOARD_E2E_ARTIFACT_DIR`(既定は一時ディレクトリ)に
    残るので、崩れたときは絵で確認できる。
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

pytestmark = pytest.mark.skipif(
    os.getenv("DASHBOARD_E2E") != "1",
    reason="実ブラウザの起動を伴うため、DASHBOARD_E2E=1 のときだけ実行する",
)

# iPhone 12/13/14 相当。家族が実際に見る幅として、いちばん狭い部類を基準にする。
MOBILE_VIEWPORT = {"width": 390, "height": 844}

REPO_SUBSYSTEM_ROOT = Path(__file__).resolve().parents[1]

# サーバー起動待ち(秒)。ラズパイ相当の遅い環境でも落ちないよう長めに取る。
STARTUP_TIMEOUT_SEC = 120


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def artifact_dir(tmp_path_factory) -> Path:
    configured = os.getenv("DASHBOARD_E2E_ARTIFACT_DIR")
    path = Path(configured) if configured else tmp_path_factory.mktemp("screenshots")
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="module")
def browser():
    playwright_api = pytest.importorskip(
        "playwright.sync_api", reason="playwright が入っていない環境ではスキップする"
    )

    # 実行ファイルの明示指定(`playwright install` 済みなら不要)。
    executable_path = os.getenv("PLAYWRIGHT_CHROMIUM_PATH") or None

    with playwright_api.sync_playwright() as p:
        instance = p.chromium.launch(executable_path=executable_path)
        yield instance
        instance.close()


# === ダッシュボード(`routers/dashboard_router.py`)===
# `unified_server` そのものを起動すると、カメラ監視・スケジューラの子プロセスまで
# 立ち上がってしまうため、ルーターだけを載せた最小サーバーで起動する。

_DASHBOARD_SERVER = """
import uvicorn
from fastapi import FastAPI
from routers import dashboard_router

app = FastAPI()
app.include_router(dashboard_router.router)
uvicorn.run(app, host="127.0.0.1", port={port}, log_level="warning")
"""


def _wait_until_ready(base_url: str, path: str, process: subprocess.Popen) -> None:
    import urllib.request

    deadline = time.time() + STARTUP_TIMEOUT_SEC
    last_error: Exception | None = None
    while time.time() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"ダッシュボードのサーバーが起動直後に終了した (exit={process.returncode})")
        try:
            with urllib.request.urlopen(f"{base_url}{path}", timeout=5) as res:
                if res.status == 200:
                    return
        except Exception as e:  # noqa: BLE001 (起動途中は接続拒否になる)
            last_error = e
        time.sleep(0.5)
    raise AssertionError(f"ダッシュボードが {STARTUP_TIMEOUT_SEC} 秒以内に応答しなかった: {last_error}")


@pytest.fixture(scope="module")
def dashboard_server(tmp_path_factory):
    """空のDB(スキーマのみ)に対してダッシュボードのサーバーを起動し、ベースURLを返す。

    データが無くてもカードは「データなし」として出る。1枚の取得が失敗しても
    `home_status_service._cached` が握るためページは返る(そのフェイルソフトも
    含めて実ブラウザで確認できる)。
    """
    import config

    db_path = tmp_path_factory.mktemp("e2e") / "home_system.db"
    env = {
        **os.environ,
        "SQLITE_DB_PATH": str(db_path),
        "NAS_MOUNT_POINT": str(tmp_path_factory.mktemp("nas")),
        "NOTIFICATION_TARGET": "none",
        "PYTHONPATH": str(REPO_SUBSYSTEM_ROOT),
    }
    subprocess.run(
        [sys.executable, "-c", "import init_unified_db; init_unified_db.init_db()"],
        cwd=REPO_SUBSYSTEM_ROOT, env=env, check=True, capture_output=True,
    )

    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-c", _DASHBOARD_SERVER.format(port=port)],
        cwd=REPO_SUBSYSTEM_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_until_ready(base_url, f"{config.DASHBOARD_BASE_PATH}/", process)
        yield base_url, config.DASHBOARD_BASE_PATH
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def _open_page(browser, url: str, *, color_scheme: str = "light"):
    context = browser.new_context(
        viewport=MOBILE_VIEWPORT,
        device_scale_factor=3,
        is_mobile=True,
        has_touch=True,
        color_scheme=color_scheme,
    )
    page = context.new_page()
    page.goto(url, wait_until="networkidle", timeout=60_000)
    page.wait_for_selector("#status", timeout=30_000)
    return context, page


@pytest.fixture(scope="module")
def home_page(browser, dashboard_server, artifact_dir):
    base_url, base_path = dashboard_server
    context, page = _open_page(browser, f"{base_url}{base_path}/")
    yield page
    page.screenshot(path=str(artifact_dir / "home.png"), full_page=True)
    context.close()


class TestHomePageLayout:
    def test_the_page_does_not_scroll_sideways(self, home_page):
        overflow = home_page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1, f"横方向に {overflow}px はみ出している"

    def test_every_card_is_a_link_to_its_detail(self, home_page):
        """A: 異常に気づいてから詳細を開くまでを1タップにする。

        枚数を直書きせず「描かれたカードの枚数」と突き合わせる。
        """
        cards = home_page.locator("a.status-card")
        rendered = home_page.locator(".status-card")

        assert rendered.count() > 0, "カードが1枚も描かれていない"
        assert cards.count() == rendered.count(), "リンクになっていないカードがある"
        for i in range(cards.count()):
            href = cards.nth(i).get_attribute("href")
            assert href and any(href.endswith(f"/{p}") for p in ("watch", "life", "sys")), (
                f"{i}枚目のリンク先が想定外: {href!r}"
            )

    def test_cards_are_large_enough_to_tap(self, home_page):
        cards = home_page.locator("a.status-card")
        for i in range(cards.count()):
            box = cards.nth(i).bounding_box()
            assert box is not None and box["height"] >= 44, f"{i}枚目が小さすぎる: {box}"

    def test_cards_are_laid_out_in_two_columns(self, home_page):
        """390px幅で1列だと8枚ぶんスクロールが要る。"""
        tops = home_page.eval_on_selector_all(
            "a.status-card", "els => els.map(el => Math.round(el.getBoundingClientRect().top))"
        )
        assert len(set(tops)) < len(tops), f"すべて別の段に並んでいる(1列になっている): {tops}"

    def test_the_alert_line_is_always_there(self, home_page):
        """B: 更新のたびに行が出たり消えたりすると、下の内容が上下に跳ねる。"""
        assert home_page.locator("p.alerts").count() == 1

    def test_the_cards_are_html_not_raw_tags(self, home_page):
        """#807 と同じ失敗(生のタグ文字列が画面に出る)をしていないこと。"""
        body_text = home_page.inner_text("body")
        assert "<div" not in body_text and "status-card" not in body_text

    def test_nav_cards_link_to_the_three_pages(self, home_page):
        """項目1: 見守り/くらし/システムへのナビカードがあること。"""
        nav = home_page.locator("a.nav-card")
        assert nav.count() == 3
        for i in range(nav.count()):
            box = nav.nth(i).bounding_box()
            assert box is not None and box["height"] >= 44

    def test_tap_targets_are_large_enough(self, home_page):
        heights = home_page.eval_on_selector_all(
            "a, button",
            "els => els.filter(e => e.offsetParent !== null)"
            ".map(e => e.getBoundingClientRect().height)",
        )
        too_small = [h for h in heights if 0 < h < 44]
        assert not too_small, f"44px 未満のタップ対象がある: {too_small}"


class TestHomePageRefresh:
    def test_updating_does_not_reload_the_whole_page(self, home_page):
        """C: 全ページ再読み込みだとスクロール位置が先頭へ戻り、画面が白く瞬く。"""
        home_page.evaluate("() => { window.__e2e_marker = 'kept'; }")
        before = home_page.locator("a.status-card").count()
        assert before > 0, "カードが1枚も描かれていない"

        # 自動更新は60秒間隔なので、同じ経路(可視状態に戻ったときの即時更新)を
        # 手で発火させて待つ。
        home_page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
        home_page.wait_for_timeout(1_500)

        assert home_page.evaluate("() => window.__e2e_marker") == "kept", (
            "ページ全体が読み込み直されている(JS側の状態が消えた)"
        )
        assert home_page.locator("#status").count() == 1, "差し替え後に差し替え先を見失っている"
        assert home_page.locator("a.status-card").count() == before, (
            "差し替え後にカードが増減している"
        )
        assert "stale" not in (home_page.get_attribute("#status", "class") or ""), (
            "更新に失敗している"
        )


class TestHomePageDarkMode:
    def test_the_page_follows_the_device_dark_setting(self, browser, dashboard_server, artifact_dir):
        """B: 夜にスマホで見ると白背景が眩しい。"""
        base_url, base_path = dashboard_server
        context, page = _open_page(browser, f"{base_url}{base_path}/", color_scheme="dark")
        try:
            background = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
            page.screenshot(path=str(artifact_dir / "home_dark.png"), full_page=True)
        finally:
            context.close()

        channels = [int(v) for v in background.replace("rgb(", "").replace(")", "").split(",")[:3]]
        assert max(channels) < 80, f"ダークモードでも背景が明るいまま: {background}"


class TestSubPagesLayout:
    """項目1: 各サブページに「ホームへ戻る」ボタンがあり、横はみ出しが無いこと。"""

    @pytest.mark.parametrize("path,heading", [("watch", "見守り"), ("life", "くらし"), ("sys", "システム")])
    def test_subpage_has_a_back_link_and_no_sideways_scroll(self, browser, dashboard_server, artifact_dir, path, heading):
        base_url, base_path = dashboard_server
        context = browser.new_context(viewport=MOBILE_VIEWPORT, device_scale_factor=3, is_mobile=True, has_touch=True)
        page = context.new_page()
        try:
            page.goto(f"{base_url}{base_path}/{path}", wait_until="networkidle", timeout=60_000)
            assert page.locator("a.back-link", has_text="ホームへ戻る").count() == 1

            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            assert overflow <= 1, f"横方向に {overflow}px はみ出している"
            page.screenshot(path=str(artifact_dir / f"{path}.png"), full_page=True)
        finally:
            context.close()
