"""
yui_mrsteiで一覧末尾のプレースホルダーカード（名前空・身長0cm、例:
profile?id=81）が 'Unknown' として登録・通知され、毎時
"Empty name extracted ... Falling back to 'Unknown'." のWARNINGを出し続けた
不具合の回帰テスト（2026-08棚卸し 課題6）。

SiteConfig.skip_unnamed_casts=True のサイトでは名前が取得できないカードを
抽出結果から除外すること、および未指定サイトでは従来どおり 'Unknown' への
フォールバックが維持されることを検証する。

DDDにはpytest基盤(conftest.py等)が無いため、本ファイルは
`pytest DDD/test_newface_monitor_parse.py` のように直接指定して実行する
(MY_HOME_SYSTEM/pytest.ini の testpaths=tests のスコープ外)。
"""
import logging
import sys
from pathlib import Path

from bs4 import BeautifulSoup

DDD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DDD_DIR))

import newface_monitor as module  # noqa: E402

SiteConfig = module.SiteConfig
WebMonitor = module.WebMonitor


# yui-mrstei.com/cast/ の実HTML構造を模したフィクスチャ。
# 末尾のカードが実際に観測されたプレースホルダー（h3空・身長0cm・背景画像）
YUI_MRSTEI_HTML = """
<ul class="gallist">
  <li class="list__item">
    <a href="/profile?id=45">
      <div class="ph"><img src="https://example.test/thumb_45.jpg" alt="白石"></div>
      <article>
        <h3>白石（しらいし）(32)</h3>
        <p class="body">身長 155 cm</p>
      </article>
    </a>
  </li>
  <li class="list__item">
    <a href="/profile?id=81">
      <div class="ph"><img src="https://example.test/back_image/24.jpg" alt=""></div>
      <article>
        <h3></h3>
        <p class="body">身長 0 cm</p>
      </article>
    </a>
  </li>
</ul>
"""


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _propagate_module_logger():
    # newface_monitorのloggerは独自ハンドラ運用でpropagate無効のため、
    # caplogで捕捉できるよう一時的にrootへの伝播を有効にする
    logger = logging.getLogger("newface_monitor")
    original = logger.propagate
    logger.propagate = True
    yield
    logger.propagate = original


def _make_site(**overrides) -> "SiteConfig":
    params = dict(
        site_id="yui_mrstei_test",
        name="Test Site",
        target_url="https://example.test/cast/",
        selector_container="ul.gallist li",
        selector_name="article h3",
        selector_link='a[href*="/profile?id="]',
        selector_image="div.ph img",
        id_query_param="id",
    )
    params.update(overrides)
    return SiteConfig(**params)


def _parse(html: str, site: "SiteConfig"):
    monitor = WebMonitor.__new__(WebMonitor)  # セッション初期化(HTTP)は不要
    soup = BeautifulSoup(html, "html.parser")
    return monitor._parse_html(soup, site)


class TestSkipUnnamedCasts:
    def test_placeholder_card_is_excluded_when_flag_enabled(self, caplog):
        site = _make_site(skip_unnamed_casts=True)
        with caplog.at_level(logging.WARNING):
            casts = _parse(YUI_MRSTEI_HTML, site)

        assert {c.id for c in casts} == {"45"}
        assert all(c.name != "Unknown" for c in casts)
        # 毎時WARNINGが出続けていた事象の再発防止: 警告を出さず静かに読み飛ばす
        assert "Empty name extracted" not in caplog.text

    def test_missing_name_element_is_also_excluded_when_flag_enabled(self):
        # 将来プレースホルダーからh3自体が消えた場合も同様に除外できること
        html = YUI_MRSTEI_HTML.replace("<h3></h3>", "")
        site = _make_site(skip_unnamed_casts=True)
        casts = _parse(html, site)
        assert {c.id for c in casts} == {"45"}

    def test_default_behavior_keeps_unknown_fallback_with_warning(self, caplog):
        # フラグ未指定サイトの従来挙動は変えない: 空テキストはWARNING付きでUnknown
        site = _make_site()
        with caplog.at_level(logging.WARNING):
            casts = _parse(YUI_MRSTEI_HTML, site)

        by_id = {c.id: c for c in casts}
        assert set(by_id) == {"45", "81"}
        assert by_id["81"].name == "Unknown"
        assert "Empty name extracted" in caplog.text

    def test_default_behavior_missing_name_element_is_silent_unknown(self, caplog):
        # name_elem自体が見つからない場合は従来どおり警告なしでUnknown
        html = YUI_MRSTEI_HTML.replace("<h3></h3>", "")
        site = _make_site()
        with caplog.at_level(logging.WARNING):
            casts = _parse(html, site)

        by_id = {c.id: c for c in casts}
        assert by_id["81"].name == "Unknown"
        assert "Empty name extracted" not in caplog.text
