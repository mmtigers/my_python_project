# DDD/test_newface_monitor_age_pattern.py
"""
Issue #413 (D-L12) の回帰テスト。

AGE_PATTERNは、括弧内の数字が「歳」「才」を伴わない場合(例: "浅見ゆき（30）")
でも無条件に年齢とみなしていたため、"(85)"のような部屋番号・順位バッジ等の
括弧付き2桁数字を誤って年齢と判定しうる懸念があった(D-L12)。

本テストは、
    1. 「歳」「才」で明示された数字は範囲に関わらず年齢として採用されること
       (サイトが明示的に年齢だとタグ付けしている情報は信頼する)
    2. 括弧のみ(suffix無し)の数字は、MonitorConfig.AGE_PLAUSIBLE_MIN/MAX の
       妥当な範囲内でのみ年齢として採用され、範囲外(例: "(85)")では
       採用されないこと
    3. 上記が_parse_html経由でも一貫して適用されること
を検証する。

DDDにはpytest基盤(conftest.py等)が無いため、本ファイルは
`pytest DDD/test_newface_monitor_age_pattern.py` のように直接指定して実行する
(MY_HOME_SYSTEM/pytest.ini の testpaths=tests のスコープ外)。
"""
import sys
from pathlib import Path

from bs4 import BeautifulSoup

DDD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DDD_DIR))

import newface_monitor as module  # noqa: E402

AGE_PATTERN = module.AGE_PATTERN
MonitorConfig = module.MonitorConfig
SiteConfig = module.SiteConfig
WebMonitor = module.WebMonitor


def _make_site(**overrides) -> "SiteConfig":
    params = dict(
        site_id="age_pattern_test",
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


def _parsed_age(html: str, site: "SiteConfig") -> str:
    monitor = WebMonitor.__new__(WebMonitor)  # セッション初期化(HTTP)は不要
    soup = BeautifulSoup(html, "html.parser")
    casts = monitor._parse_html(soup, site)
    assert len(casts) == 1
    return next(iter(casts)).age


def _card_html(name_html: str) -> str:
    return f"""
    <ul class="gallist">
      <li class="list__item">
        <a href="/profile?id=1">
          <div class="ph"><img src="https://example.test/thumb.jpg" alt=""></div>
          <article>
            <h3>{name_html}</h3>
          </article>
        </a>
      </li>
    </ul>
    """


class TestAgePatternGroups:
    """正規表現自体の挙動(グループの意味)を直接検証する。"""

    def test_bracket_with_suffix_captures_number_and_suffix(self):
        m = AGE_PATTERN.search("うるは(23歳)")
        assert m.groups() == ("23", "歳", None)

    def test_bracket_without_suffix_captures_number_with_none_suffix(self):
        m = AGE_PATTERN.search("浅見ゆき（30）")
        assert m.groups() == ("30", None, None)

    def test_plain_number_with_suffix_captures_third_group(self):
        m = AGE_PATTERN.search("小鳥(ことり)セラピスト  22歳")
        assert m.groups() == (None, None, "22")

    def test_single_digit_bracket_number_does_not_match(self):
        # 1桁の括弧数字(ランキングバッジ等)は元々マッチしない
        assert AGE_PATTERN.search("人気ランキング(1)位") is None


class TestAgeExtractionRespectsPlausibilityForBracketOnly:
    """suffix無しの括弧数字は妥当な年齢範囲内でのみ採用されること。"""

    def test_explicit_suffix_is_trusted_regardless_of_range(self):
        site = _make_site()
        # 85歳は妥当範囲(AGE_PLAUSIBLE_MAX=79)の外だが、「歳」で明示されている
        # ため無条件に信頼して採用する。
        age = _parsed_age(_card_html("ベテラン(85歳)"), site)
        assert age == "85"

    def test_bracket_only_number_within_range_is_accepted(self):
        site = _make_site()
        age = _parsed_age(_card_html("浅見ゆき（30）"), site)
        assert age == "30"

    def test_bracket_only_number_out_of_range_is_rejected(self):
        """D-L12回帰確認: レビューで指摘された"(85)"のような括弧付き2桁数字
        (suffix無し)は、妥当な年齢範囲外であれば年齢として採用しないこと。"""
        site = _make_site()
        age = _parsed_age(_card_html("部屋(85)ゆき"), site)
        assert age == ""

    def test_bracket_only_number_at_range_boundaries_is_accepted(self):
        site = _make_site()
        assert _parsed_age(
            _card_html(f"下限({MonitorConfig.AGE_PLAUSIBLE_MIN})"), site
        ) == str(MonitorConfig.AGE_PLAUSIBLE_MIN)
        assert _parsed_age(
            _card_html(f"上限({MonitorConfig.AGE_PLAUSIBLE_MAX})"), site
        ) == str(MonitorConfig.AGE_PLAUSIBLE_MAX)


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
