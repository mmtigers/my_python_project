# DDD/test_newface_monitor_datamanager.py
"""
restpiaサイトのknown_casts_*.jsonキャッシュが 'utf-8' codec can't decode byte
... : invalid start byte で毎時CRITICALを出し続けた不具合の回帰テスト。

DataManager.load_known_casts()は元々 (json.JSONDecodeError, IOError) しか
捕捉しておらず、UnicodeDecodeError(ValueErrorのサブクラスでIOErrorではない)を
捕捉できなかったため、同じ破損ファイルへの読み込み失敗が毎回未処理の例外として
伝播し、自動復旧が一切行われなかった。本テストは、
    1. 非UTF-8データによる破損ファイルを安全に読み込み失敗として処理できること
    2. 破損ファイルが退避(quarantine)され、次回以降は読み込み対象から外れること
    3. 直近のバックアップ(.bak)から自動復旧できること
    4. save_known_castsが保存の度にバックアップを更新すること
を検証する。

DDDにはpytest基盤(conftest.py等)が無いため、本ファイルは
`pytest DDD/test_newface_monitor_datamanager.py` のように直接指定して実行する
(MY_HOME_SYSTEM/pytest.ini の testpaths=tests のスコープ外)。
"""
import sys
from pathlib import Path

DDD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DDD_DIR))

import newface_monitor as module  # noqa: E402

SiteConfig = module.SiteConfig
CastMember = module.CastMember
DataManager = module.DataManager


def _make_site(data_filename: str) -> "SiteConfig":
    return SiteConfig(
        site_id="restpia_test",
        name="Test Site",
        target_url="https://example.test/therapist.html",
        selector_container="div",
        selector_name="li",
        selector_link="a",
        selector_image="a",
        data_filename=data_filename,
    )


class TestLoadKnownCastsCorruption:
    def test_non_utf8_bytes_are_treated_as_load_failure_and_return_empty_set(
        self, tmp_path, monkeypatch
    ):
        site = _make_site("known_casts_restpia_test.json")
        monkeypatch.setattr(module.MonitorConfig, "get_data_dir", staticmethod(lambda: tmp_path))

        data_file = tmp_path / site.get_data_filename()
        # 実際のCRITICALログと同じ症状(0xf9は不正な開始バイト)を再現する
        data_file.write_bytes(b'[{"id": "1", "name": "\xf9broken"}]')

        result = DataManager.load_known_casts(site)

        assert result == set()

    def test_corrupted_file_is_quarantined_so_it_is_not_reparsed_next_run(
        self, tmp_path, monkeypatch
    ):
        site = _make_site("known_casts_restpia_test.json")
        monkeypatch.setattr(module.MonitorConfig, "get_data_dir", staticmethod(lambda: tmp_path))

        data_file = tmp_path / site.get_data_filename()
        data_file.write_bytes(b'[{"id": "1", "name": "\xf9broken"}]')

        DataManager.load_known_casts(site)

        assert not data_file.exists()
        quarantined = list(tmp_path.glob(f"{data_file.name}.corrupted-*"))
        assert len(quarantined) == 1
        assert quarantined[0].read_bytes() == b'[{"id": "1", "name": "\xf9broken"}]'

        # 退避済みなので、次回の読み込みは「ファイルなし」として扱われる
        second_result = DataManager.load_known_casts(site)
        assert second_result == set()

    def test_recovers_from_backup_when_primary_file_is_corrupted(self, tmp_path, monkeypatch):
        site = _make_site("known_casts_restpia_test.json")
        monkeypatch.setattr(module.MonitorConfig, "get_data_dir", staticmethod(lambda: tmp_path))

        data_file = tmp_path / site.get_data_filename()
        backup_file = data_file.with_suffix(data_file.suffix + ".bak")
        backup_file.write_text(
            '[{"id": "1", "name": "Alice", "detail_url": "u", "image_url": "i", "age": "20"}]',
            encoding="utf-8",
        )
        data_file.write_bytes(b'[{"id": "1", "name": "\xf9broken"}]')

        result = DataManager.load_known_casts(site)

        assert result == {CastMember(id="1", name="Alice", detail_url="u", image_url="i", age="20")}


class TestSaveKnownCastsBackup:
    def test_save_keeps_previous_version_as_backup(self, tmp_path, monkeypatch):
        site = _make_site("known_casts_restpia_test.json")
        monkeypatch.setattr(module.MonitorConfig, "get_data_dir", staticmethod(lambda: tmp_path))

        first = {CastMember(id="1", name="Alice", detail_url="u1", image_url="i1", age="20")}
        DataManager.save_known_casts(site, first)

        second = {CastMember(id="2", name="Bob", detail_url="u2", image_url="i2", age="25")}
        DataManager.save_known_casts(site, second)

        data_file = tmp_path / site.get_data_filename()
        backup_file = data_file.with_suffix(data_file.suffix + ".bak")

        assert DataManager.load_known_casts(site) == second
        assert backup_file.exists()
        assert DataManager._read_casts_file(backup_file) == first

    def test_save_does_not_create_backup_on_first_write(self, tmp_path, monkeypatch):
        site = _make_site("known_casts_restpia_test.json")
        monkeypatch.setattr(module.MonitorConfig, "get_data_dir", staticmethod(lambda: tmp_path))

        DataManager.save_known_casts(
            site, {CastMember(id="1", name="Alice", detail_url="u", image_url="i", age="20")}
        )

        data_file = tmp_path / site.get_data_filename()
        backup_file = data_file.with_suffix(data_file.suffix + ".bak")
        assert data_file.exists()
        assert not backup_file.exists()


class TestLoadDailySummaryCorruption:
    """Issue #174の回帰テスト: load_daily_summaryはload_known_castsと同じ
    「非UTF-8破損でUnicodeDecodeError(IOErrorのサブクラスではなくValueErrorの
    サブクラス)が未捕捉のまま伝播する」バグを持っていた。伝播すると
    record_daily_new_casts経由でsave_known_castsまで到達できず、
    毎時同じキャストが「新規」として再通知され続ける無限反復を招く。"""

    def test_non_utf8_bytes_are_treated_as_load_failure_and_return_empty_dict(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(module.MonitorConfig, "get_data_dir", staticmethod(lambda: tmp_path))

        summary_file = tmp_path / "daily_summary.json"
        # 実際のCRITICALログと同じ症状(0xf9は不正な開始バイト)を再現する
        summary_file.write_bytes(b'{"date": "2026-08-30", "\xf9broken": 1}')

        result = DataManager.load_daily_summary()

        assert result == {}

    def test_record_daily_new_casts_completes_despite_corrupted_summary_file(
        self, tmp_path, monkeypatch
    ):
        """load_daily_summaryが例外を送出しないため、record_daily_new_casts
        (延いてはこれを呼ぶ_check_site)が破損ファイルによって中断せず、
        後続のsave_known_castsまで到達できることを確認する。"""
        monkeypatch.setattr(module.MonitorConfig, "get_data_dir", staticmethod(lambda: tmp_path))

        summary_file = tmp_path / "daily_summary.json"
        summary_file.write_bytes(b'{"date": "2026-08-30", "\xf9broken": 1}')

        # 例外を送出せずに完走すること自体が回帰確認の対象
        DataManager.record_daily_new_casts("restpia_test", 3)

        # 破損ファイルは新しい正常な集計データで上書きされている
        result = DataManager.load_daily_summary()
        assert result["counts"]["restpia_test"] == 3


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
