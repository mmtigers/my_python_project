# MY_HOME_SYSTEM/tests/test_dashboard_mobile_e2e.py
"""スマートフォン幅の実画面に対する回帰テスト(Playwright)。

なぜ必要か:
    2026-09 の #807 / #822 は、いずれも「実機のスマホで開いて初めて分かった」
    崩れだった(サマリーカードが生HTMLとして出る、固定ヘッダーが半透明で本文が
    透ける、先頭の行がヘッダーの下に隠れる)。既存の回帰テストはCSS文字列や
    描画関数の呼び出しを見るもので、**実際に描かれた結果の座標**は見ていない。
    そのため「CSSは入っているのにレイアウトは崩れている」を拾えない。

    ここでは Streamlit を実際に起動し、390x844(iPhone相当)のビューポートで
    開いて、レイアウトの不変条件を検証する。

実行方法:
    ブラウザのダウンロードと Streamlit の起動を伴うため、既定ではスキップする。

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
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

pytestmark = pytest.mark.skipif(
    os.getenv("DASHBOARD_E2E") != "1",
    reason="実ブラウザと Streamlit の起動を伴うため、DASHBOARD_E2E=1 のときだけ実行する",
)

# iPhone 12/13/14 相当。家族が実際に見る幅として、いちばん狭い部類を基準にする。
MOBILE_VIEWPORT = {"width": 390, "height": 844}

REPO_SUBSYSTEM_ROOT = Path(__file__).resolve().parents[1]

# Streamlit の起動待ち(秒)。ラズパイ相当の遅い環境でも落ちないよう長めに取る。
STARTUP_TIMEOUT_SEC = 120


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_healthy(port: int, process: subprocess.Popen) -> None:
    """Streamlit の `_stcore/health` が ok を返すまで待つ。"""
    import urllib.request

    deadline = time.time() + STARTUP_TIMEOUT_SEC
    last_error: Exception | None = None
    while time.time() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"Streamlit が起動直後に終了した (exit={process.returncode})")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=2) as res:
                if res.status == 200:
                    return
        except Exception as e:  # noqa: BLE001 (起動途中は接続拒否になる)
            last_error = e
        time.sleep(0.5)
    raise AssertionError(f"Streamlit が {STARTUP_TIMEOUT_SEC} 秒以内に起動しなかった: {last_error}")


@pytest.fixture(scope="module")
def dashboard_url(tmp_path_factory):
    """一時DB(スキーマだけ)に対して Streamlit を起動し、URLを返す。"""
    db_path = tmp_path_factory.mktemp("e2e") / "home_system.db"
    env = {
        **os.environ,
        "SQLITE_DB_PATH": str(db_path),
        "NAS_MOUNT_POINT": str(tmp_path_factory.mktemp("nas")),
        "NOTIFICATION_TARGET": "none",
        "PYTHONPATH": str(REPO_SUBSYSTEM_ROOT),
    }

    # マイグレーションを当てた空のDBを用意する(データが無くても画面は出る)。
    subprocess.run(
        [sys.executable, "-c", "import init_unified_db; init_unified_db.init_db()"],
        cwd=REPO_SUBSYSTEM_ROOT, env=env, check=True, capture_output=True,
    )

    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", "dashboard.py",
            "--server.port", str(port),
            "--server.address", "127.0.0.1",
            "--server.headless", "true",
            "--browser.gatherUsageStats", "false",
        ],
        cwd=REPO_SUBSYSTEM_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        _wait_until_healthy(port, process)
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture(scope="module")
def artifact_dir(tmp_path_factory) -> Path:
    configured = os.getenv("DASHBOARD_E2E_ARTIFACT_DIR")
    path = Path(configured) if configured else tmp_path_factory.mktemp("screenshots")
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="module")
def browser():
    """このモジュール全体で1つだけ開くブラウザ。

    `sync_playwright()` は自前のイベントループを持つため、1プロセスで入れ子に
    開くと「Playwright Sync API inside the asyncio loop」で失敗する。
    Streamlit 側と軽量ページ側でフィクスチャを分けず、ここに集約する。
    """
    playwright_api = pytest.importorskip(
        "playwright.sync_api", reason="playwright が入っていない環境ではスキップする"
    )

    # 実行ファイルの明示指定(`playwright install` 済みなら不要)。
    executable_path = os.getenv("PLAYWRIGHT_CHROMIUM_PATH") or None

    with playwright_api.sync_playwright() as p:
        instance = p.chromium.launch(executable_path=executable_path)
        yield instance
        instance.close()


@pytest.fixture(scope="module")
def mobile_page(browser, dashboard_url, artifact_dir):
    """390x844 のモバイルビューポートでダッシュボードを開いたページ。"""
    context = browser.new_context(
        viewport=MOBILE_VIEWPORT,
        device_scale_factor=3,
        is_mobile=True,
        has_touch=True,
    )
    page = context.new_page()
    page.goto(dashboard_url, wait_until="networkidle", timeout=60_000)
    # 初回描画(JR運行情報の取得を含む)が終わるまで待つ
    page.wait_for_selector('[data-testid="stAppViewContainer"]', timeout=60_000)
    page.wait_for_timeout(2_000)
    yield page
    page.screenshot(path=str(artifact_dir / "home.png"), full_page=True)
    context.close()


class TestMobileLayout:
    def test_the_page_does_not_scroll_sideways(self, mobile_page):
        """横にはみ出すと、縦スクロール中に画面が左右へ揺れて操作しづらい。"""
        overflow = mobile_page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1, f"横方向に {overflow}px はみ出している"

    def test_the_first_row_is_not_hidden_under_the_fixed_header(self, mobile_page):
        """#822 の再発防止。Streamlit 既定のヘッダーは position:fixed で、
        本文の上パディングが足りないと先頭の行が最初から隠れる。"""
        header = mobile_page.locator('[data-testid="stHeader"]').first
        header_bottom = header.evaluate("el => el.getBoundingClientRect().bottom")

        refresh_button = mobile_page.get_by_role("button", name="データを更新").first
        button_top = refresh_button.evaluate("el => el.getBoundingClientRect().top")

        assert button_top >= header_bottom - 1, (
            f"先頭のボタン(top={button_top})が固定ヘッダー(bottom={header_bottom})の下に隠れている"
        )

    def test_the_header_actions_stay_on_one_row(self, mobile_page):
        """「データを更新」と「ファミクエを開く」が縦に積まれると、
        ヘッダーが2段になってタブと本文を画面外へ押し下げる。"""
        refresh = mobile_page.get_by_role("button", name="データを更新").first
        quest = mobile_page.get_by_role("link", name="ファミクエを開く").first

        refresh_box = refresh.bounding_box()
        quest_box = quest.bounding_box()
        assert abs(refresh_box["y"] - quest_box["y"]) < refresh_box["height"], (
            "ヘッダーの操作列が縦に積まれている"
        )

    def test_tap_targets_are_large_enough(self, mobile_page):
        """iOS/Android の推奨タップターゲット(44px)を下回るボタンを作らない。"""
        heights = mobile_page.eval_on_selector_all(
            '[data-testid="stMain"] button, [data-testid="stMain"] a[data-testid*="LinkButton"]',
            "els => els.filter(e => e.offsetParent !== null)"
            ".map(e => e.getBoundingClientRect().height)",
        )
        too_small = [h for h in heights if 0 < h < 44]
        assert not too_small, f"44px 未満のボタンがある: {too_small}"

    def test_summary_cards_are_rendered_as_html_not_as_text(self, mobile_page):
        """#807 の再発防止。カードのHTMLが生の文字列として画面に出ていないこと。"""
        body_text = mobile_page.inner_text("body")
        assert "status-card" not in body_text
        assert mobile_page.locator(".status-card").count() > 0

    def test_summary_cards_are_laid_out_in_two_columns(self, mobile_page):
        """1列だと9枚でスクロールが長くなり、3列だと1枚が潰れて値が読めない。"""
        tops = mobile_page.eval_on_selector_all(
            ".status-card", "els => els.map(e => Math.round(e.getBoundingClientRect().top))"
        )
        assert tops, "サマリーカードが1枚も描かれていない"
        assert len(set(tops)) < len(tops), "カードが1列に並んでいる(2列で折り返していない)"


    def test_every_tab_is_visible_without_scrolling_sideways(self, mobile_page):
        """目的のタブを探せないのは、10タブ構成で一番困っていた点。
        5つが画面内に収まっていること。"""
        chips = mobile_page.locator('[data-testid="stButtonGroup"] button')
        assert chips.count() == 5

        viewport_width = MOBILE_VIEWPORT["width"]
        for index in range(chips.count()):
            box = chips.nth(index).bounding_box()
            assert box["x"] >= 0 and box["x"] + box["width"] <= viewport_width + 1, (
                f"{index + 1}番目のタブが画面外にある: {box}"
            )

    def test_tab_labels_are_not_truncated(self, mobile_page):
        """「おで…」「シス…」のように切れると、どのタブか読み取れない。"""
        overflows = mobile_page.eval_on_selector_all(
            '[data-testid="stButtonGroup"] button p',
            "els => els.map(e => ({text: e.innerText, over: e.scrollWidth - e.clientWidth}))",
        )
        truncated = [o for o in overflows if o["over"] > 0]
        assert not truncated, f"ラベルが切れているタブがある: {truncated}"


class TestMobileNavigation:
    def test_switching_tabs_keeps_the_choice_in_the_url(self, mobile_page):
        """タブが `?tab=` に残ることで、更新・再接続でホームに戻らず、
        `/dashboard?tab=watch` をホーム画面に置ける。"""
        mobile_page.locator('[data-testid="stButtonGroup"] button', has_text="システム").first.click()
        mobile_page.wait_for_timeout(1_500)

        assert "tab=sys" in mobile_page.url

    def test_deep_link_opens_that_tab_directly(self, dashboard_url, mobile_page):
        mobile_page.goto(f"{dashboard_url}/?tab=watch", wait_until="networkidle", timeout=60_000)
        mobile_page.wait_for_timeout(2_000)

        assert mobile_page.get_by_text("カメラ・ギャラリー").count() > 0


class TestHomeScreenInstall:
    """ホーム画面に追加するための指定が、実際のHTMLに入っていること。

    差し込みは `unified_server.py`(8000番)の中継層で行うため、ここで見ている
    8501番の直叩きには**入らない**。中継経路の検証は
    tests/test_dashboard_proxy.py の TestMobileHeadInjection が担当する。
    """

    def test_streamlit_itself_serves_a_head_to_inject_into(self, mobile_page):
        head_html = mobile_page.evaluate("() => document.head.outerHTML")
        assert "</head>" in head_html + "</head>"
        assert mobile_page.locator("head title").count() > 0


# === 軽量ページ(`{DASHBOARD_BASE_PATH}/m`)===
# こちらは Streamlit ではなく FastAPI(`routers/dashboard_router.py`)が直接返す。
# 上の Streamlit 用フィクスチャとは別に、ルーターだけを載せたサーバーを起動する
# (`unified_server` そのものを起動すると、カメラ監視・スケジューラの子プロセスまで
#  立ち上がってしまうため)。

_LIGHT_PAGE_SERVER = """
import uvicorn
from fastapi import FastAPI
from routers import dashboard_router

app = FastAPI()
app.include_router(dashboard_router.router)
uvicorn.run(app, host="127.0.0.1", port={port}, log_level="warning")
"""


def _wait_until_light_page_ready(base_url: str, path: str, process: subprocess.Popen) -> None:
    import urllib.request

    deadline = time.time() + STARTUP_TIMEOUT_SEC
    last_error: Exception | None = None
    while time.time() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"軽量ページのサーバーが起動直後に終了した (exit={process.returncode})")
        try:
            with urllib.request.urlopen(f"{base_url}{path}", timeout=5) as res:
                if res.status == 200:
                    return
        except Exception as e:  # noqa: BLE001 (起動途中は接続拒否になる)
            last_error = e
        time.sleep(0.5)
    raise AssertionError(f"軽量ページが {STARTUP_TIMEOUT_SEC} 秒以内に応答しなかった: {last_error}")


@pytest.fixture(scope="module")
def light_page_url(tmp_path_factory):
    """空のDB(スキーマのみ)に対して軽量ページのサーバーを起動し、ページのURLを返す。

    データが無くてもカード9枚は「データなし」として出る。JR運行情報の取得は
    ネットワークが無ければ失敗するが、`_cached` が握って「情報取得不可」になるだけで
    ページは返る(そのフェイルソフトも含めて実ブラウザで確認できる)。
    """
    import config

    db_path = tmp_path_factory.mktemp("e2e_light") / "home_system.db"
    env = {
        **os.environ,
        "SQLITE_DB_PATH": str(db_path),
        "NAS_MOUNT_POINT": str(tmp_path_factory.mktemp("nas_light")),
        "NOTIFICATION_TARGET": "none",
        "PYTHONPATH": str(REPO_SUBSYSTEM_ROOT),
    }
    subprocess.run(
        [sys.executable, "-c", "import init_unified_db; init_unified_db.init_db()"],
        cwd=REPO_SUBSYSTEM_ROOT, env=env, check=True, capture_output=True,
    )

    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-c", _LIGHT_PAGE_SERVER.format(port=port)],
        cwd=REPO_SUBSYSTEM_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    mobile_path = f"{config.DASHBOARD_BASE_PATH}/m"
    try:
        _wait_until_light_page_ready(base_url, mobile_path, process)
        yield f"{base_url}{mobile_path}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def _open_light_page(browser, url: str, artifact_dir: Path, *, color_scheme: str):
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
def light_page(browser, light_page_url, artifact_dir):
    context, page = _open_light_page(browser, light_page_url, artifact_dir, color_scheme="light")
    yield page
    page.screenshot(path=str(artifact_dir / "light_page.png"), full_page=True)
    context.close()


class TestLightPageLayout:
    def test_the_page_does_not_scroll_sideways(self, light_page):
        overflow = light_page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1, f"横方向に {overflow}px はみ出している"

    def test_every_card_is_a_link_to_its_detail(self, light_page):
        """A: 異常に気づいてから詳細を開くまでを1タップにする。"""
        cards = light_page.locator("a.status-card")

        assert cards.count() == 9
        for i in range(cards.count()):
            href = cards.nth(i).get_attribute("href")
            assert href and "?tab=" in href, f"{i}枚目にリンク先が無い: {href!r}"

    def test_cards_are_large_enough_to_tap(self, light_page):
        """カード自体がタップ先になったので、44pxを下回らないこと。"""
        cards = light_page.locator("a.status-card")
        for i in range(cards.count()):
            box = cards.nth(i).bounding_box()
            assert box is not None and box["height"] >= 44, f"{i}枚目が小さすぎる: {box}"

    def test_cards_are_laid_out_in_two_columns(self, light_page):
        """390px幅で1列だと9枚ぶんスクロールが要る。"""
        tops = light_page.eval_on_selector_all(
            "a.status-card", "els => els.map(el => Math.round(el.getBoundingClientRect().top))"
        )
        assert len(set(tops)) < len(tops), f"すべて別の段に並んでいる(1列になっている): {tops}"

    def test_the_alert_line_is_always_there(self, light_page):
        """B: 更新のたびに行が出たり消えたりすると、下の内容が上下に跳ねる。"""
        assert light_page.locator("p.alerts").count() == 1

    def test_the_cards_are_html_not_raw_tags(self, light_page):
        """#807 と同じ失敗(生のタグ文字列が画面に出る)をしていないこと。"""
        body_text = light_page.inner_text("body")
        assert "<div" not in body_text and "status-card" not in body_text


class TestLightPageRefresh:
    def test_updating_does_not_reload_the_whole_page(self, light_page):
        """C: 全ページ再読み込みだとスクロール位置が先頭へ戻り、画面が白く瞬く。"""
        light_page.evaluate("() => { window.__e2e_marker = 'kept'; }")

        # 自動更新は60秒間隔なので、同じ経路(可視状態に戻ったときの即時更新)を
        # 手で発火させて待つ。
        light_page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
        light_page.wait_for_timeout(1_500)

        assert light_page.evaluate("() => window.__e2e_marker") == "kept", (
            "ページ全体が読み込み直されている(JS側の状態が消えた)"
        )
        assert light_page.locator("#status").count() == 1, "差し替え後に差し替え先を見失っている"
        assert light_page.locator("a.status-card").count() == 9
        assert "stale" not in (light_page.get_attribute("#status", "class") or ""), (
            "更新に失敗している"
        )


class TestLightPageDarkMode:
    def test_the_page_follows_the_device_dark_setting(self, browser, light_page_url, artifact_dir):
        """B: 夜にスマホで見ると白背景が眩しい。"""
        context, page = _open_light_page(browser, light_page_url, artifact_dir, color_scheme="dark")
        try:
            background = page.evaluate(
                "() => getComputedStyle(document.body).backgroundColor"
            )
            channels = [int(v) for v in background.replace("rgb(", "").replace(")", "").split(",")[:3]]
            page.screenshot(path=str(artifact_dir / "light_page_dark.png"), full_page=True)
        finally:
            context.close()

        assert max(channels) < 80, f"ダークモードでも背景が明るいまま: {background}"
