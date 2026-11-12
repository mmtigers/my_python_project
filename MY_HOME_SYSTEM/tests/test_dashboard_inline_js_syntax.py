# MY_HOME_SYSTEM/tests/test_dashboard_inline_js_syntax.py
"""ダッシュボードの外部 CSS / JS(`static/dashboard/`)の構文検査と配信・参照の検査(Issue #949)。

ダッシュボードの JS / CSS は、もとは `dashboard_page_service.py` の Python 文字列リテラルで、
ESLint・構文チェックの対象外だった(正規表現の二重エスケープの取り違えが本番で初めて JS の
構文エラーになる)。#949 で `static/dashboard/` の外部ファイルへ切り出したので、ここでは
そのファイルを `node --check` にかけ、CSS は波括弧の対応を確認する。あわせて、各ページが
正しい順序で参照すること・ルーターが配信することを固定する。

node が無い環境(開発機など)では JS の構文検査をスキップする。CI(ubuntu-latest)には入っている。
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from services import dashboard_page_service as dps

_STATIC = Path(dps._STATIC_DIR)
# 同梱した外部ライブラリ(*.min.js)は自作コードの検査(<script>の混入等)の対象外にする。
_JS_FILES = sorted(n for n in dps.STATIC_ASSET_FILES if n.endswith(".js") and not n.endswith(".min.js"))


def test_static_dir_matches_allowlist():
    """許可リストと実ファイルの食い違い(置き忘れ・消し忘れ)を検知する。"""
    assert {p.name for p in _STATIC.iterdir()} == set(dps.STATIC_ASSET_FILES)


@pytest.mark.skipif(shutil.which("node") is None, reason="node が無い環境では JS の構文検査をスキップする")
@pytest.mark.parametrize("name", _JS_FILES)
def test_static_js_has_valid_syntax(name):
    result = subprocess.run(["node", "--check", str(_STATIC / name)], capture_output=True, text=True)
    assert result.returncode == 0, f"{name} に構文エラーがあります:\n{result.stderr}"


@pytest.mark.parametrize("name", _JS_FILES)
def test_static_js_has_no_script_tags(name):
    """インライン時代の`<script>`タグを外部ファイルへ持ち込んでいない。"""
    assert "<script" not in (_STATIC / name).read_text(encoding="utf-8")


def test_dashboard_css_braces_are_balanced():
    css = re.sub(r"/\*.*?\*/", "", (_STATIC / "dashboard.css").read_text(encoding="utf-8"), flags=re.S)
    assert css.count("{") == css.count("}"), "dashboard.css の波括弧の数が合っていません"
    assert css.count("{") > 50, "CSS を取り出せていません(ルールが極端に少ない)"


def _srcs(html: str) -> list[str]:
    return re.findall(r'<script src="([^"]+)"', html)


def test_shell_links_stylesheet_before_status_card_css():
    html = dps._page_shell("t", "<p>x</p>")
    link = html.index('<link rel="stylesheet" href="/dashboard/static/dashboard.css?v=')
    assert link < html.index(".status-card")  # 共通CSSの後にカード用CSS(従来と同じカスケード順)
    assert "<style>" in html


def test_camera_script_order_hls_then_camera_js():
    srcs = _srcs(dps._camera_script())
    # hls.js はCDNではなく同梱のファイルを読む(インターネットが切れてもLAN内でライブを見られるように)
    assert srcs[0].startswith("/dashboard/static/hls.light.min.js?v=")
    assert srcs[1].startswith("/dashboard/static/camera.js?v=")
    assert not any(src.startswith("http") for src in srcs)


def test_sys_page_script_order_maintenance_then_git():
    # システムページは maintenance.js → git_status.js の順(従来の連結順)
    html = dps._page_shell(
        "t", "x", extra_head=dps._script_tag("maintenance.js") + dps._script_tag("git_status.js")
    )
    srcs = _srcs(html)
    # 先頭は全ページ共通の app.js(テーマ切替・Service Worker登録)
    assert [s.split("?")[0].rsplit("/", 1)[1] for s in srcs] == ["app.js", "maintenance.js", "git_status.js"]


def test_vendored_hls_is_excluded_from_our_own_js_checks_but_is_served():
    assert dps.resolve_static_asset("hls.light.min.js") is not None
    assert (_STATIC / "hls.LICENSE.txt").is_file(), "同梱した hls.js のライセンス文が無い"


def _dark_tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", block))


def test_dark_theme_tokens_match_between_auto_and_manual():
    """暗い配色のトークンは「OSに従う(メディアクエリ)」と「手動(data-theme=dark)」の2か所にある。
    片方だけ直すと、手動で暗くした端末だけ色が違う、という気づきにくい食い違いになる。"""
    css = (_STATIC / "dashboard.css").read_text(encoding="utf-8")
    auto = css[css.index(':root:not([data-theme="light"]) {'):]
    auto = auto[: auto.index("}")]
    manual = css[css.index(':root[data-theme="dark"] {'):]
    manual = manual[: manual.index("}")]
    assert _dark_tokens(auto), "暗い配色のトークンを取り出せていません"
    assert _dark_tokens(auto) == _dark_tokens(manual)


def test_shell_applies_saved_theme_before_stylesheet_and_has_toggle():
    html = dps._page_shell("t", "<p>x</p>")
    assert html.index("dashTheme") < html.index('<link rel="stylesheet"'), "保存済みテーマの適用が描画より後になっている"
    assert 'id="themeToggle"' in html


def test_service_worker_only_handles_navigations():
    """オフライン画面用のService Workerは、ページの読み込み以外(API・映像・画像)に関与しない。"""
    sw = (_STATIC / "sw.js").read_text(encoding="utf-8")
    assert 'event.request.mode !== "navigate"' in sw
    assert "caches." not in sw, "ページやデータをキャッシュしない(古い家の状態を見せない)"


def test_resolve_static_asset_rejects_unknown_and_traversal():
    assert dps.resolve_static_asset("camera.js") is not None
    for bad in ("../config.py", "..%2fconfig.py", "nope.js", "", "dashboard.css/../camera.js"):
        assert dps.resolve_static_asset(bad) is None


@pytest.mark.parametrize("name,ctype", sorted(dps.STATIC_ASSET_FILES.items()))
def test_router_serves_static_assets(api_client, name, ctype):
    r = api_client.get(f"/dashboard/static/{name}")
    assert r.status_code == 200
    assert r.headers["content-type"] == ctype
    assert r.headers["cache-control"] == "no-cache"
    assert r.content == (_STATIC / name).read_bytes()


def test_router_static_404_for_unlisted(api_client):
    assert api_client.get("/dashboard/static/unified_server.py").status_code == 404
    assert api_client.get("/dashboard/static/..%2f..%2fconfig.py").status_code == 404


@pytest.mark.parametrize("page,names", [
    ("/dashboard/watch", ["dashboard.css", "camera.js"]),
    ("/dashboard/sys", ["dashboard.css", "maintenance.js", "git_status.js"]),
])
def test_pages_reference_existing_assets(api_client, page, names):
    html = api_client.get(page).text
    for n in names:
        m = re.search(rf'(?:href|src)="(/dashboard/static/{re.escape(n)}\?v=\d+)"', html)
        assert m, f"{page} が {n} を参照していません"
        assert api_client.get(m.group(1)).status_code == 200
