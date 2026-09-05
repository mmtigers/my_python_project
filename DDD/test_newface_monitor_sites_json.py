# DDD/test_newface_monitor_sites_json.py
"""
Issue #413 の回帰テスト。

`MonitorConfig.SITES`（従来は本体ファイル内に約970行のPythonリテラルとして
直書きされていた79サイト分の設定）を `sites.json` へ外出しした際の、以下2点を
検証する。

    1. sites.json から読み込んだ SITES が、外出し前の既知サイト数件分の
       フィールド値と一致すること（データが壊れずに移行されたことの確認）。
    2. `_load_sites` が、壊れた sites.json（JSON構文エラー・配列でない・
       要素がオブジェクトでない・必須フィールド欠落・未知フィールド・
       site_id重複）に対して、黙ってスキップせず例外を送出すること。

DDDにはpytest基盤(conftest.py等)が無いため、本ファイルは
`pytest DDD/test_newface_monitor_sites_json.py` のように直接指定して実行する
(MY_HOME_SYSTEM/pytest.ini の testpaths=tests のスコープ外)。
"""
import json
import sys
from pathlib import Path

import pytest

DDD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DDD_DIR))

import newface_monitor as module  # noqa: E402

SiteConfig = module.SiteConfig
MonitorConfig = module.MonitorConfig
_load_sites = module._load_sites


def _by_id(site_id: str) -> SiteConfig:
    for site in MonitorConfig.SITES:
        if site.site_id == site_id:
            return site
    raise AssertionError(f"site_id={site_id!r} が MonitorConfig.SITES に見つかりません")


class TestSitesJsonLoadsIdentically:
    """(a) sites.json から読み込んだ内容が、外出し前の既知サイトと一致すること。"""

    def test_site_count_is_79(self):
        # Issue #413 起票時点の既知サイト数(79件)がそのまま維持されていること。
        assert len(MonitorConfig.SITES) == 79

    def test_petitpetit_dream_matches_known_values(self):
        # data_filename を明示指定している後方互換サイト(既存運用データ対応)。
        site = _by_id("petitpetit_dream")
        assert site.name == "ぷちぷちどりーむ"
        assert site.target_url == "https://petitpetit-dream.com/newface/"
        assert site.selector_container == "ul.gallist li"
        assert site.selector_name == "article h3 a"
        assert site.selector_link == "article h3 a"
        assert site.selector_image == "div.ph img:not(.list_today)"
        assert site.data_filename == "known_casts.json"
        assert site.id_query_param is None
        assert site.image_attr == "src"
        assert site.image_from_style is False

    def test_osaka_milktea_matches_known_values(self):
        # id_query_param を指定しているサイト。
        site = _by_id("osaka_milktea")
        assert site.name == "ミルクティー -milktea-"
        assert site.target_url == "https://osakamilktea.com/sp/newface.php"
        assert site.selector_link == 'a[href*="profile.php"]'
        assert site.id_query_param == "id"
        assert site.data_filename == ""  # 未指定=デフォルト

    def test_itadaki_matches_known_values(self):
        # image_attr をデフォルトの'src'から変更しているサイト(lazyload対応)。
        site = _by_id("itadaki")
        assert site.image_attr == "data-original"
        assert site.image_from_style is False

    def test_all_sites_have_unique_site_id(self):
        ids = [s.site_id for s in MonitorConfig.SITES]
        assert len(ids) == len(set(ids))

    def test_get_data_filename_defaults_use_site_id(self):
        # data_filename未指定サイトは "known_casts_{site_id}.json" にフォールバックする
        # (SiteConfig.get_data_filename のロジックはJSON化後も一切変更していない)。
        site = _by_id("merci_spa")
        assert site.data_filename == ""
        assert site.get_data_filename() == "known_casts_merci_spa.json"


class TestLoadSitesFailsLoudly:
    """(b) sites.json が壊れている場合、_load_sites が例外を送出すること。"""

    def _write(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "sites.json"
        p.write_text(content, encoding="utf-8")
        return p

    def test_missing_file_raises(self, tmp_path: Path):
        missing = tmp_path / "does_not_exist.json"
        with pytest.raises(RuntimeError, match="読み込めません"):
            _load_sites(missing)

    def test_invalid_json_syntax_raises(self, tmp_path: Path):
        path = self._write(tmp_path, "{not valid json")
        with pytest.raises(RuntimeError, match="JSONが不正"):
            _load_sites(path)

    def test_top_level_not_a_list_raises(self, tmp_path: Path):
        path = self._write(tmp_path, json.dumps({"site_id": "x"}))
        with pytest.raises(RuntimeError, match="配列である必要"):
            _load_sites(path)

    def test_entry_not_an_object_raises(self, tmp_path: Path):
        path = self._write(tmp_path, json.dumps(["not-an-object"]))
        with pytest.raises(RuntimeError, match="オブジェクトではありません"):
            _load_sites(path)

    def test_missing_required_field_raises(self, tmp_path: Path):
        # target_url が欠落している不正エントリ。
        entry = {
            "site_id": "broken",
            "name": "Broken Site",
            "selector_container": "div",
            "selector_name": "a",
            "selector_link": "a",
            "selector_image": "img",
        }
        path = self._write(tmp_path, json.dumps([entry]))
        with pytest.raises(RuntimeError, match="broken"):
            _load_sites(path)

    def test_unknown_field_raises(self, tmp_path: Path):
        entry = {
            "site_id": "broken",
            "name": "Broken Site",
            "target_url": "https://example.test/",
            "selector_container": "div",
            "selector_name": "a",
            "selector_link": "a",
            "selector_image": "img",
            "not_a_real_field": True,
        }
        path = self._write(tmp_path, json.dumps([entry]))
        with pytest.raises(RuntimeError, match="broken"):
            _load_sites(path)

    def test_duplicate_site_id_raises(self, tmp_path: Path):
        def make(site_id: str) -> dict:
            return {
                "site_id": site_id,
                "name": "Dup",
                "target_url": "https://example.test/",
                "selector_container": "div",
                "selector_name": "a",
                "selector_link": "a",
                "selector_image": "img",
            }

        path = self._write(tmp_path, json.dumps([make("dup"), make("dup")]))
        with pytest.raises(RuntimeError, match="重複"):
            _load_sites(path)

    def test_comment_field_is_ignored_and_valid_entry_loads(self, tmp_path: Path):
        # _comment はドキュメント専用フィールドであり、正常なエントリの構築を妨げない。
        entry = {
            "site_id": "ok",
            "name": "OK Site",
            "target_url": "https://example.test/",
            "selector_container": "div",
            "selector_name": "a",
            "selector_link": "a",
            "selector_image": "img",
            "_comment": "これはドキュメント用の注記であり構築に使われない",
        }
        path = self._write(tmp_path, json.dumps([entry]))
        sites = _load_sites(path)
        assert len(sites) == 1
        assert sites[0].site_id == "ok"

    def test_empty_list_loads_zero_sites(self, tmp_path: Path):
        # 空配列自体はエラーではない(全サイト削除も正当な運用操作のため)。
        path = self._write(tmp_path, "[]")
        assert _load_sites(path) == []
