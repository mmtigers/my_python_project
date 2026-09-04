# DDD/test_newface_monitor_datamanager.py
"""
restpiaサイトのknown_casts_*.jsonキャッシュが 'utf-8' codec can't decode byte
... : invalid start byte で毎時CRITICALを出し続けた不具合の回帰テスト。

dm.load_known_casts()は元々 (json.JSONDecodeError, IOError) しか
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
from unittest.mock import MagicMock

import pytest

DDD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DDD_DIR))

import newface_monitor as module  # noqa: E402

SiteConfig = module.SiteConfig
CastMember = module.CastMember
# #364: DataManagerは静的メソッド群から、解決済みdata_dirを束縛するインスタンスへ
# 変更された。各テストは MonitorConfig.get_data_dir のmonkeypatchではなく
# DataManager(tmp_path) を直接生成して使う。
DataManager = module.DataManager


def _fixed_datetime(monkeypatch, initial):
    """module.datetime.now()を任意の時刻に固定するdatetimeサブクラスを注入する。
    戻り値の`_now`属性を書き換えることでテスト中に時刻を進められる。"""
    class _FixedDatetime(module.datetime):
        _now = initial

        @classmethod
        def now(cls):
            return cls._now

    monkeypatch.setattr(module, "datetime", _FixedDatetime)
    return _FixedDatetime


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
        dm = DataManager(tmp_path)

        data_file = tmp_path / site.get_data_filename()
        # 実際のCRITICALログと同じ症状(0xf9は不正な開始バイト)を再現する
        data_file.write_bytes(b'[{"id": "1", "name": "\xf9broken"}]')

        result = dm.load_known_casts(site)

        assert result == set()

    def test_corrupted_file_is_quarantined_so_it_is_not_reparsed_next_run(
        self, tmp_path, monkeypatch
    ):
        site = _make_site("known_casts_restpia_test.json")
        dm = DataManager(tmp_path)

        data_file = tmp_path / site.get_data_filename()
        data_file.write_bytes(b'[{"id": "1", "name": "\xf9broken"}]')

        dm.load_known_casts(site)

        assert not data_file.exists()
        quarantined = list(tmp_path.glob(f"{data_file.name}.corrupted-*"))
        assert len(quarantined) == 1
        assert quarantined[0].read_bytes() == b'[{"id": "1", "name": "\xf9broken"}]'

        # 退避済みなので、次回の読み込みは「ファイルなし」として扱われる
        second_result = dm.load_known_casts(site)
        assert second_result == set()

    def test_recovers_from_backup_when_primary_file_is_corrupted(self, tmp_path, monkeypatch):
        site = _make_site("known_casts_restpia_test.json")
        dm = DataManager(tmp_path)

        data_file = tmp_path / site.get_data_filename()
        backup_file = data_file.with_suffix(data_file.suffix + ".bak")
        backup_file.write_text(
            '[{"id": "1", "name": "Alice", "detail_url": "u", "image_url": "i", "age": "20"}]',
            encoding="utf-8",
        )
        data_file.write_bytes(b'[{"id": "1", "name": "\xf9broken"}]')

        result = dm.load_known_casts(site)

        assert result == {CastMember(id="1", name="Alice", detail_url="u", image_url="i", age="20")}


class TestLoadKnownCastsContentErrorsAreQuarantined:
    """Issue #365: 隔離(.corrupted-*)は内容起因の失敗(JSONDecodeError/
    UnicodeDecodeError/TypeError/KeyError)に限られること。"""

    def test_json_syntax_error_is_quarantined_and_returns_empty_set(self, tmp_path):
        site = _make_site("known_casts_restpia_test.json")
        dm = DataManager(tmp_path)
        data_file = tmp_path / site.get_data_filename()
        data_file.write_text('[{"id": "1", "name": "truncated', encoding="utf-8")

        assert dm.load_known_casts(site) == set()
        assert not data_file.exists()
        assert len(list(tmp_path.glob(f"{data_file.name}.corrupted-*"))) == 1

    def test_schema_mismatch_type_error_is_quarantined(self, tmp_path):
        site = _make_site("known_casts_restpia_test.json")
        dm = DataManager(tmp_path)
        data_file = tmp_path / site.get_data_filename()
        # CastMember(**item) が受け付けないキー → TypeError
        data_file.write_text('[{"unexpected_field": 1}]', encoding="utf-8")

        assert dm.load_known_casts(site) == set()
        assert not data_file.exists()
        assert len(list(tmp_path.glob(f"{data_file.name}.corrupted-*"))) == 1


class TestLoadKnownCastsTransientIOErrorIsNotQuarantined:
    """Issue #365 (D-H2) の回帰テスト。

    以前は _LOAD_ERRORS(OSError含む)を捕捉した後、種別を問わず
    data_file.rename(quarantine_path) していたため、CIFS/autofs の瞬断
    (EIO/ENOENT/ETIMEDOUT)で open() が失敗しただけで中身が正しいファイルが
    .corrupted-* に退避され、.bak が無ければ空集合→全キャスト再通知、
    以降は union 保存されるため隔離前のデータ(退店済み含む)が永久に
    戻らなかった。OSError はログのみ出して当該サイトをスキップすること。
    """

    @staticmethod
    def _raise_eio(_path):
        raise OSError(5, "Input/output error")

    def test_os_error_raises_known_casts_unavailable_and_keeps_file(self, tmp_path, monkeypatch):
        site = _make_site("known_casts_restpia_test.json")
        dm = DataManager(tmp_path)
        data_file = tmp_path / site.get_data_filename()
        original = '[{"id": "1", "name": "Alice", "detail_url": "u", "image_url": "i", "age": "20"}]'
        data_file.write_text(original, encoding="utf-8")
        monkeypatch.setattr(module.DataManager, "_read_casts_file", staticmethod(self._raise_eio))

        with pytest.raises(module.KnownCastsUnavailableError):
            dm.load_known_casts(site)

        # 正常なファイルが隔離されず、そのまま残っていること
        assert data_file.exists()
        assert data_file.read_text(encoding="utf-8") == original
        assert list(tmp_path.glob(f"{data_file.name}.corrupted-*")) == []

    def test_check_site_skips_fetch_notify_and_save_on_io_error(self, tmp_path, monkeypatch):
        """_check_site は I/O エラー時に空集合で続行せず、巡回・通知・保存の
        いずれも行わずに当該サイトをスキップすること。"""
        site = _make_site("known_casts_restpia_test.json")
        dm = DataManager(tmp_path)
        data_file = tmp_path / site.get_data_filename()
        data_file.write_text('[{"id": "1", "name": "Alice", "detail_url": "u", "image_url": "i"}]', encoding="utf-8")
        monkeypatch.setattr(module.DataManager, "_read_casts_file", staticmethod(self._raise_eio))

        monitor = MagicMock()
        notifier = MagicMock()
        mock_save = MagicMock()
        monkeypatch.setattr(module.DataManager, "save_known_casts", mock_save)

        module._check_site(monitor, notifier, site, dm)

        monitor.fetch_current_casts.assert_not_called()
        notifier.notify.assert_not_called()
        mock_save.assert_not_called()
        assert data_file.exists()


class TestSaveKnownCastsBackup:
    def test_save_keeps_previous_version_as_backup(self, tmp_path, monkeypatch):
        site = _make_site("known_casts_restpia_test.json")
        dm = DataManager(tmp_path)

        first = {CastMember(id="1", name="Alice", detail_url="u1", image_url="i1", age="20")}
        dm.save_known_casts(site, first)

        second = {CastMember(id="2", name="Bob", detail_url="u2", image_url="i2", age="25")}
        dm.save_known_casts(site, second)

        data_file = tmp_path / site.get_data_filename()
        backup_file = data_file.with_suffix(data_file.suffix + ".bak")

        assert dm.load_known_casts(site) == second
        assert backup_file.exists()
        assert DataManager._read_casts_file(backup_file) == first

    def test_save_does_not_create_backup_on_first_write(self, tmp_path, monkeypatch):
        site = _make_site("known_casts_restpia_test.json")
        dm = DataManager(tmp_path)

        dm.save_known_casts(
            site, {CastMember(id="1", name="Alice", detail_url="u", image_url="i", age="20")}
        )

        data_file = tmp_path / site.get_data_filename()
        backup_file = data_file.with_suffix(data_file.suffix + ".bak")
        assert data_file.exists()
        assert not backup_file.exists()

    def test_backup_write_is_atomic_via_tmp_and_replace(self, tmp_path, monkeypatch):
        """D-L7: .bakの更新はwrite_bytesによる直接上書きではなく、
        他の永続化と同じtmp書き込み+replaceのアトミックパターンで行うこと。
        書き込み中に中断しても既存の.bakが破損しないことを保証する。"""
        site = _make_site("known_casts_restpia_test.json")
        dm = DataManager(tmp_path)

        first = {CastMember(id="1", name="Alice", detail_url="u1", image_url="i1", age="20")}
        dm.save_known_casts(site, first)  # 初回はdata_file未存在のため.bakは作られない

        second = {CastMember(id="2", name="Bob", detail_url="u2", image_url="i2", age="25")}
        dm.save_known_casts(site, second)  # .bak = first の内容になる

        data_file = tmp_path / site.get_data_filename()
        backup_file = data_file.with_suffix(data_file.suffix + ".bak")
        bak_tmp_file = backup_file.with_suffix(backup_file.suffix + ".tmp")

        original_write_bytes = Path.write_bytes

        def _raise_after_partial_write(self, data):
            # 実際のディスク書き込み中断を模して、bak_tmp_fileへの書き込み自体は
            # 発生させつつ例外を送出する(.bakの直接write_bytesだった場合と違い、
            # 中断してもbak_tmp_fileという別名のファイルにしか影響しないことを
            # 確認するのが本テストの主眼)。
            original_write_bytes(self, data)
            raise OSError("simulated write failure while updating .bak")

        monkeypatch.setattr(Path, "write_bytes", _raise_after_partial_write)

        third = {CastMember(id="3", name="Carol", detail_url="u3", image_url="i3", age="30")}
        dm.save_known_casts(site, third)  # .bak更新(second相当への更新)は失敗するはず

        # .bak本体は(書き込み失敗のため)更新されず、直前の状態(firstのバックアップ)
        # のまま残る(直接write_bytesだった場合は破損した内容で上書きされうった)。
        assert DataManager._read_casts_file(backup_file) == first
        # 失敗した一時ファイルが残置されないこと
        assert not bak_tmp_file.exists()
        # 本体データ(data_file)自体は正常にthirdへ更新されていること
        # (.bak更新の失敗は警告ログに留め、保存処理全体を失敗させない)
        assert dm.load_known_casts(site) == third

    def test_verification_failure_does_not_leave_tmp_file_behind(self, tmp_path, monkeypatch):
        """D-L8: tmpファイルの読戻し検証に失敗した場合、以前は.json.tmpが
        削除されずディレクトリに残り続けていた。best-effortで削除すること。"""
        site = _make_site("known_casts_restpia_test.json")
        dm = DataManager(tmp_path)

        def _raise_on_verify(_path):
            raise ValueError("simulated verification failure")

        monkeypatch.setattr(module.DataManager, "_read_casts_file", staticmethod(_raise_on_verify))

        dm.save_known_casts(
            site, {CastMember(id="1", name="Alice", detail_url="u", image_url="i", age="20")}
        )

        data_file = tmp_path / site.get_data_filename()
        tmp_path_file = data_file.with_suffix(data_file.suffix + ".tmp")
        assert not tmp_path_file.exists()
        assert not data_file.exists()  # 検証失敗のためreplaceまで到達していない


class TestLoadDailySummaryCorruption:
    """Issue #174の回帰テスト: load_daily_summaryはload_known_castsと同じ
    「非UTF-8破損でUnicodeDecodeError(IOErrorのサブクラスではなくValueErrorの
    サブクラス)が未捕捉のまま伝播する」バグを持っていた。伝播すると
    record_daily_new_casts経由でsave_known_castsまで到達できず、
    毎時同じキャストが「新規」として再通知され続ける無限反復を招く。"""

    def test_non_utf8_bytes_are_treated_as_load_failure_and_return_empty_dict(
        self, tmp_path, monkeypatch
    ):
        dm = DataManager(tmp_path)

        summary_file = tmp_path / "daily_summary.json"
        # 実際のCRITICALログと同じ症状(0xf9は不正な開始バイト)を再現する
        summary_file.write_bytes(b'{"date": "2026-08-30", "\xf9broken": 1}')

        result = dm.load_daily_summary()

        assert result == {}

    def test_record_daily_new_casts_completes_despite_corrupted_summary_file(
        self, tmp_path, monkeypatch
    ):
        """load_daily_summaryが例外を送出しないため、record_daily_new_casts
        (延いてはこれを呼ぶ_check_site)が破損ファイルによって中断せず、
        後続のsave_known_castsまで到達できることを確認する。"""
        dm = DataManager(tmp_path)

        summary_file = tmp_path / "daily_summary.json"
        summary_file.write_bytes(b'{"date": "2026-08-30", "\xf9broken": 1}')

        # 例外を送出せずに完走すること自体が回帰確認の対象
        dm.record_daily_new_casts("restpia_test", 3)

        # 破損ファイルは新しい正常な集計データで上書きされている
        result = dm.load_daily_summary()
        assert result["counts"]["restpia_test"] == 3


class TestDailySummaryLateCountsNotLost:
    """Issue #183の回帰テスト: 以前はrecord_daily_new_castsがカレンダー日付変更時に
    集計を無条件リセットしていたため、(1) 21時台のサマリ送信後(22時〜24時)に
    検知した件数が送信済み扱いのまま加算され続け、翌日最初の検知時のリセットで
    どのサマリにも計上されずに消える、(2) 21時台の実行自体が無かった日は
    追い付き送信もできずその日の集計が丸ごと失われる、という2つの過少報告
    経路があった。日付によるリセットを廃止し、_maybe_send_daily_summaryが
    実際に送信した直後にのみ集計をクリアするよう修正した。"""

    def test_record_daily_new_casts_accumulates_across_calendar_date_change(
        self, tmp_path, monkeypatch
    ):
        """カレンダー日付をまたいで呼び出しても、以前存在したような
        日付ベースのリセットは行われず単純加算され続けること。"""
        dm = DataManager(tmp_path)
        fixed_dt = _fixed_datetime(monkeypatch, module.datetime(2026, 8, 30, 23, 0, 0))

        dm.record_daily_new_casts("restpia_test", 2)  # 8/30 23:00

        fixed_dt._now = module.datetime(2026, 8, 31, 0, 30, 0)  # 日付が変わった直後
        dm.record_daily_new_casts("restpia_test", 3)
        dm.record_daily_new_casts("other_site", 1)

        result = dm.load_daily_summary()
        assert result["counts"] == {"restpia_test": 5, "other_site": 1}

    def test_counts_after_send_are_carried_over_to_next_send_not_lost(
        self, tmp_path, monkeypatch
    ):
        """21時台の送信後(22時・23時)に検知した件数、および日付をまたいで
        蓄積した件数が、次回の送信でまとめて送られること(以前は日付変更時の
        リセットで消えていた)。"""
        dm = DataManager(tmp_path)
        fixed_dt = _fixed_datetime(monkeypatch, module.datetime(2026, 8, 30, 21, 0, 0))

        notifier = MagicMock()
        # 1回目の21時台送信(件数0)
        module._maybe_send_daily_summary(notifier, dm)
        assert notifier.notify_daily_summary.call_count == 1

        # 送信後(22時・23時)に検知 -> 以前はこの分が翌日のリセットで消えていた
        dm.record_daily_new_casts("restpia_test", 4)
        dm.record_daily_new_casts("restpia_test", 1)
        # 日付をまたいでさらに検知
        dm.record_daily_new_casts("other_site", 2)

        # 翌日21時台に送信
        fixed_dt._now = module.datetime(2026, 8, 31, 21, 0, 0)
        module._maybe_send_daily_summary(notifier, dm)

        assert notifier.notify_daily_summary.call_count == 2
        sent_counts = notifier.notify_daily_summary.call_args.args[0]
        assert sent_counts == {"restpia_test": 5, "other_site": 2}

        # 送信後はリセットされていること
        result = dm.load_daily_summary()
        assert result["counts"] == {}

    def test_missed_21h_run_does_not_lose_accumulated_counts(self, tmp_path, monkeypatch):
        """21時台の実行自体が無かった日(cron欠落・ロック競合等)でも、
        次に成功した21時台の実行でまとめて送信され取りこぼされないこと。"""
        dm = DataManager(tmp_path)
        fixed_dt = _fixed_datetime(monkeypatch, module.datetime(2026, 8, 30, 15, 0, 0))

        # 21時台の実行が無かった日(8/30)の日中に検知
        # (以前は翌日の送信処理内で「送信対象の集計データはdate==today_strのものだけ」
        # という判定によりこの分が空扱いされ、丸ごと失われていた)
        dm.record_daily_new_casts("restpia_test", 3)

        fixed_dt._now = module.datetime(2026, 8, 31, 21, 0, 0)  # 翌日21時台の実行
        notifier = MagicMock()
        module._maybe_send_daily_summary(notifier, dm)

        sent_counts = notifier.notify_daily_summary.call_args.args[0]
        assert sent_counts == {"restpia_test": 3}


class TestDailySummarySendFailureDoesNotLoseCounts:
    """Issue #226の回帰テスト: notify_daily_summaryがWebhook未設定/送信失敗で
    Falseを返した場合、_maybe_send_daily_summaryは集計をクリアせず、
    last_sent_dateも更新しないこと(同日中の再送機会を残すため)。"""

    def test_send_failure_keeps_counts_and_allows_retry_same_day(self, tmp_path, monkeypatch):
        dm = DataManager(tmp_path)
        _fixed_datetime(monkeypatch, module.datetime(2026, 8, 30, 21, 0, 0))

        dm.record_daily_new_casts("restpia_test", 3)

        notifier = MagicMock()
        notifier.notify_daily_summary.return_value = False  # Webhook失敗を模す

        module._maybe_send_daily_summary(notifier, dm)

        # 送信失敗時は集計がクリアされず、last_sent_dateも更新されないこと
        result = dm.load_daily_summary()
        assert result["counts"] == {"restpia_test": 3}
        assert result.get("last_sent_date") != "2026-08-30"

        # 送信失敗時はガード節(last_sent_date==today_str)に引っかからず、
        # 同日中の再実行で再送を試みられること
        notifier.notify_daily_summary.return_value = True
        module._maybe_send_daily_summary(notifier, dm)

        assert notifier.notify_daily_summary.call_count == 2
        sent_counts = notifier.notify_daily_summary.call_args.args[0]
        assert sent_counts == {"restpia_test": 3}

        result = dm.load_daily_summary()
        assert result["counts"] == {}
        assert result["last_sent_date"] == "2026-08-30"

    def test_send_success_still_clears_counts(self, tmp_path, monkeypatch):
        """回帰防止: Falseケースの追加が成功時の既存挙動(#183)を壊していないこと。"""
        dm = DataManager(tmp_path)
        _fixed_datetime(monkeypatch, module.datetime(2026, 8, 30, 21, 0, 0))

        dm.record_daily_new_casts("restpia_test", 3)

        notifier = MagicMock()
        notifier.notify_daily_summary.return_value = True

        module._maybe_send_daily_summary(notifier, dm)

        result = dm.load_daily_summary()
        assert result["counts"] == {}
        assert result["last_sent_date"] == "2026-08-30"


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
