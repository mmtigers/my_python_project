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


class TestFallbackCastIdIsStableAcrossVolatileAttributes:
    """Issue #538: フォールバック ID のフィンガープリントは画像 URL(と名前)のみを材料にし、
    lazyload 状態やバッジ等の揮発的な属性で毎回変わらないこと。"""

    def _site(self):
        return module.SiteConfig(
            site_id="fp_test", name="FP", target_url="https://example.test/list",
            selector_container="div.cast", selector_name="span.name", selector_link="a.none",
            selector_image="img",
        )

    def _div(self, html):
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "html.parser").select_one("div.cast")

    def test_volatile_attribute_does_not_change_id(self):
        site = self._site()
        a = self._div('<div class="cast" data-loaded="false"><span class="name">Alice</span><img src="/img/a.jpg"><span class="badge">NEW</span></div>')
        b = self._div('<div class="cast" data-loaded="true" data-nonce="x1"><span class="name">Alice</span><img src="/img/a.jpg"></div>')
        _, id_a = module.WebMonitor._extract_cast_link_and_id(a, site, "Alice")
        _, id_b = module.WebMonitor._extract_cast_link_and_id(b, site, "Alice")
        assert id_a == id_b
        assert id_a.startswith("name_Alice_")

    def test_different_image_gives_different_id(self):
        site = self._site()
        a = self._div('<div class="cast"><span class="name">Alice</span><img src="/img/a.jpg"></div>')
        b = self._div('<div class="cast"><span class="name">Alice</span><img src="/img/b.jpg"></div>')
        assert module.WebMonitor._extract_cast_link_and_id(a, site, "Alice")[1] != module.WebMonitor._extract_cast_link_and_id(b, site, "Alice")[1]


class TestExtractCastAgeSkipsImplausibleLeadingNumber:
    """Issue #589の回帰テスト。

    以前はAGE_PATTERN.search()で最初の一致のみを見ていたため、
    "No.(12) さくら(25歳)"のように、年齢より前に(歳/才の無い)2桁の
    括弧数字(連番・部屋番号等)が出現すると、その数字がD-L12の妥当性範囲
    チェック(AGE_PLAUSIBLE_MIN〜MAX)で却下された時点で検索を打ち切り、
    後続の本来の年齢表記を一切試さないまま年齢抽出自体が失敗していた。
    finditer()で候補を出現順にすべて走査し、妥当性チェックを通過する
    最初の候補が見つかるまで後続の候補も試すことを検証する。
    """

    def _elem(self, html):
        return BeautifulSoup(html, "html.parser").select_one("h3")

    def test_finds_age_after_an_implausible_leading_bracket_number(self):
        # "(12)"は歳/才の明示が無く、AGE_PLAUSIBLE_MIN(18)未満のため却下される。
        # 後続の"(25歳)"を正しく採用できること。
        elem = self._elem("<h3>No.(12) さくら(25歳)</h3>")
        assert WebMonitor._extract_cast_age(elem) == "25"

    def test_still_prefers_the_first_plausible_or_explicit_match(self):
        elem = self._elem("<h3>白石（しらいし）(32)</h3>")
        assert WebMonitor._extract_cast_age(elem) == "32"

    def test_leading_implausible_number_with_no_valid_age_anywhere_yields_empty(self):
        # 妥当な年齢表記が最後まで見つからない場合は空文字のままであること
        # (誤って"(12)"を年齢として採用しない)。
        elem = self._elem("<h3>No.(12) さくら</h3>")
        assert WebMonitor._extract_cast_age(elem) == ""

    def test_explicit_suffix_is_trusted_even_if_out_of_plausible_range(self):
        # 「歳」「才」が明示されている場合は妥当性範囲チェックを経由せず
        # 無条件に信頼する(D-L12の既存挙動を維持していることの確認)。
        elem = self._elem("<h3>べテラン(85歳)</h3>")
        assert WebMonitor._extract_cast_age(elem) == "85"
