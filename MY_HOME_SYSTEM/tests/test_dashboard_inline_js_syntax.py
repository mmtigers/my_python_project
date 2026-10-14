# MY_HOME_SYSTEM/tests/test_dashboard_inline_js_syntax.py
"""dashboard_page_service.py に Python 文字列として埋め込まれた JS / CSS の構文検査(Issue #949)。

ダッシュボードの JS(`_CAMERA_SCRIPT` / `_GIT_STATUS_SCRIPT` / `_MAINTENANCE_SCRIPT`)と
CSS(`_PAGE_BASE_CSS`)は Python の文字列リテラルなので、ESLint・整形・構文チェックの対象外で、
正規表現の二重エスケープ(`\\\\d` 等)の取り違えなどが本番で JS の構文エラーになるまで
気付けない。この検査は、埋め込み JS を取り出して `node --check` にかけ、CSS は波括弧の対応を
確認する。CSS/JS を外部ファイルへ切り出すか(Issue #949)は別の設計判断で、本テストは
どちらの構成でも使える(取り出し元だけ差し替える)。

node が無い環境(開発機など)では JS の検査をスキップする。CI(ubuntu-latest)には入っている。
"""
import re
import shutil
import subprocess

import pytest

from services import dashboard_page_service as dps

_INLINE_SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)

_JS_ASSETS = {
    "_CAMERA_SCRIPT": dps._CAMERA_SCRIPT,
    "_GIT_STATUS_SCRIPT": dps._GIT_STATUS_SCRIPT,
    "_MAINTENANCE_SCRIPT": dps._MAINTENANCE_SCRIPT,
}


@pytest.mark.skipif(shutil.which("node") is None, reason="node が無い環境では JS の構文検査をスキップする")
@pytest.mark.parametrize("name", sorted(_JS_ASSETS))
def test_embedded_js_has_valid_syntax(name, tmp_path):
    blocks = _INLINE_SCRIPT_RE.findall(_JS_ASSETS[name])
    assert blocks, f"{name} にインラインの <script> が見つかりません(取り出し方が実装と食い違っています)"
    for i, block in enumerate(blocks):
        path = tmp_path / f"{name}_{i}.js"
        path.write_text(block, encoding="utf-8")
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, f"{name} の {i + 1} 番目の <script> に構文エラーがあります:\n{result.stderr}"


def test_page_base_css_braces_are_balanced():
    css = re.sub(r"/\*.*?\*/", "", dps._PAGE_BASE_CSS, flags=re.S)
    assert css.count("{") == css.count("}"), "_PAGE_BASE_CSS の波括弧の数が合っていません"
    assert css.count("{") > 50, "CSS を取り出せていません(ルールが極端に少ない)"
