# MY_HOME_SYSTEM/tests/test_dashboard_mobile_header.py
"""ダッシュボード最上部のスマホ表示の回帰テスト。

2026-09-21 に実機のスマホで2点確認した。

1. Streamlit 既定のヘッダー(`»` と `⋮` の行)は `position: fixed` かつ半透明で、
   スクロールした本文がその下を通ると**透けて重なり読めなくなる**。
   `views/dashboard/common.py` の CSS には `stHeader` への指定が1つも無かった。
2. ヘッダーの操作列(更新 / ファミクエ)が縦に積まれ、ヘッダーが2段になって
   タブと本文を画面外へ押し下げていた。モバイルCSSが**すべての**
   `stHorizontalBlock` を 100% 幅に強制していたため。

2 の修正は「`dashboard.py` が `st.container(key=...)` で付けた
`.st-key-<key>` クラスを、CSS 側がセレクタで拾う」という**文字列の一致**に
依存している。どちらかをリネームしても Python は通り Streamlit も警告を出さず、
**スマホのレイアウトだけが静かに元へ戻る**。その一致をここで固定する。
"""
import os
import re
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from views.dashboard import common as view_common

_DASHBOARD_PY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard.py"
)


def _dashboard_source() -> str:
    with open(_DASHBOARD_PY, encoding="utf-8") as f:
        return f.read()


def _keyed_container_sources() -> str:
    """`st.container(key=...)` が書かれうるファイルをすべて連結して返す。

    当初は `dashboard.py` だけだったが、スマホ向けのレイアウト例外
    (写真ギャラリーを2列にする等)は View 側にも置くようになった。
    """
    sources = [_dashboard_source()]
    views_dir = os.path.join(os.path.dirname(_DASHBOARD_PY), "views", "dashboard")
    for name in sorted(os.listdir(views_dir)):
        if name.endswith(".py"):
            with open(os.path.join(views_dir, name), encoding="utf-8") as f:
                sources.append(f.read())
    return "\n".join(sources)


def _mobile_media_block() -> str:
    """モバイル幅のメディアクエリの中身だけを取り出す。

    PC 幅にも効く指定と混ざったまま検査すると、「スマホ幅で効いていること」を
    検証したつもりで実際は別の場所の指定を見ている、という取り違えが起きる。
    """
    css = view_common.CUSTOM_CSS
    marker = f"@media (max-width: {view_common.MOBILE_BREAKPOINT_PX}px)"
    start = css.index(marker)
    # メディアクエリの開き波括弧から、対応する閉じ波括弧までを数えて切り出す。
    brace_start = css.index("{", start + len(marker))
    depth = 0
    for i in range(brace_start, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[brace_start:i + 1]
    raise AssertionError("モバイル用メディアクエリの波括弧が閉じていない")


class TestHeaderActionsKeyMatchesCss:
    """`st.container(key=...)` と CSS セレクタの文字列が一致していること。"""

    def test_dashboard_uses_a_keyed_container_for_the_action_row(self):
        src = _dashboard_source()
        assert re.search(r'st\.container\(\s*key\s*=\s*["\']header_actions["\']', src), (
            "ヘッダー操作列が st.container(key=\"header_actions\") で囲われていない。"
            "囲いを外すとモバイルCSSの一律100%化が効き、ボタンが縦積みに戻る"
        )

    def test_css_targets_that_exact_key(self):
        block = _mobile_media_block()
        assert ".st-key-header_actions" in block, (
            "モバイルCSSが .st-key-header_actions を拾っていない。"
            "key をリネームしたらこのセレクタも揃えること"
        )

    def test_the_two_strings_are_the_same(self):
        """片方だけリネームされていないこと(この不一致は無言で効かなくなる)。"""
        src = _keyed_container_sources()
        keys_in_py = set(re.findall(r'st\.container\(\s*key\s*=\s*["\']([^"\']+)["\']', src))
        keys_in_css = set(re.findall(r'\.st-key-([A-Za-z0-9_-]+)', view_common.CUSTOM_CSS))
        assert keys_in_css, "CSS に .st-key-* のセレクタが無い"
        assert keys_in_css <= keys_in_py, (
            f"CSS が参照する key {sorted(keys_in_css - keys_in_py)} が "
            "dashboard.py / views/dashboard/ に存在しない"
        )


class TestActionRowStaysHorizontalOnMobile:
    """短いボタン2つは、スマホ幅でも横並びのままにすること。"""

    def test_columns_are_not_forced_to_full_width_inside_the_action_row(self):
        block = _mobile_media_block()
        # 一律ルール(グラフ・表向け)は残っていること
        assert "flex: 1 1 100% !important" in block, (
            "stHorizontalBlock の一律100%化が消えている。"
            "グラフ・表はスマホ幅で縦積みでなければ読めない"
        )
        # そのうえで、操作列だけは打ち消していること
        override = block[block.index(".st-key-header_actions"):]
        assert "flex: 1 1 0 !important" in override
        assert "min-width: 0 !important" in override

    def test_labels_do_not_wrap(self):
        """幅が半分になるぶん、ラベルが折り返して背が高くならないこと。"""
        block = _mobile_media_block()
        override = block[block.index(".st-key-header_actions"):]
        assert "white-space: nowrap" in override


class TestStreamlitHeaderIsOpaqueOnMobile:
    """固定ヘッダーの下を本文が通っても滲まないこと。"""

    def test_header_has_an_opaque_background(self):
        block = _mobile_media_block()
        assert '[data-testid="stHeader"]' in block, (
            "Streamlit 既定ヘッダーへの指定が無い。半透明のままだと"
            "スクロールした本文が透けて重なる"
        )
        header_rule = block[block.index('[data-testid="stHeader"]'):]
        assert "background: #ffffff" in header_rule
        # backdrop-filter を消さないと、不透明にしてもぼかしが残る
        assert "backdrop-filter: none" in header_rule

    def test_only_applies_at_mobile_width(self):
        """PC 幅では既定の見た目を変えないこと(画面が広く重なりが問題にならない)。"""
        css = view_common.CUSTOM_CSS
        block = _mobile_media_block()
        outside = css.replace(block, "")
        assert '[data-testid="stHeader"]' not in outside


class TestExistingMobileRulesSurvive:
    """今回の変更で、既存のスマホ対応を壊していないこと。"""

    def test_tap_targets_are_still_44px(self):
        assert "min-height: 44px" in view_common.CUSTOM_CSS

    def test_tab_list_is_still_scrollable(self):
        block = _mobile_media_block()
        assert '[data-baseweb="tab-list"]' in block
        assert "overflow-x: auto" in block


class TestContentStartsBelowTheFixedHeader:
    """ページを開いた時点で、先頭の行が固定ヘッダーの下に隠れないこと(#822)。

    以前はモバイル幅の `.block-container` の `padding-top` が 1.2rem(約19px)で、
    約 3.75rem(60px)ある Streamlit の固定ヘッダーより小さく、開いた直後から
    「データを更新」「ファミクエを開く」の上半分が隠れていた。ヘッダーを不透明に
    しただけでは「透けて見える」が「隠れて見えない」に変わるだけだった。
    """

    # Streamlit のヘッダー高さ(版によって 2.875rem〜3.75rem)。大きい方を下限にする。
    STREAMLIT_HEADER_REM = 3.75

    def test_mobile_top_padding_clears_the_header(self):
        block = _mobile_media_block()
        rule = block[block.index(".block-container"):]
        rule = rule[: rule.index("}")]
        m = re.search(r"padding-top:\s*([0-9.]+)rem", rule)
        assert m, "モバイル幅の .block-container に rem 指定の padding-top が無い"
        assert float(m.group(1)) >= self.STREAMLIT_HEADER_REM, (
            f"padding-top {m.group(1)}rem は固定ヘッダー({self.STREAMLIT_HEADER_REM}rem)より小さく、"
            "開いた時点で先頭の行がヘッダーの下に隠れる"
        )
