# MY_HOME_SYSTEM/tests/test_train_service.py
"""
services/train_service.py のテスト。

Low修正の回帰防止: get_jr_traffic_status() は以前、API取得に失敗した際も
「🟢 平常運転」をデフォルトとして返しており、実際には運行情報を確認できて
いないだけなのに画面上は「異常なし」に見えてしまい、遅延見逃しに直結する
問題があった。取得不可(is_unavailable=True)を平常運転とは明確に区別する。
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services import train_service


class TestGetJrTrafficStatus:
    def test_api_failure_returns_unavailable_not_normal_operation(self, monkeypatch):
        monkeypatch.setattr(
            train_service.requests, "get", MagicMock(side_effect=Exception("network error"))
        )

        result = train_service.get_jr_traffic_status()

        for line_name in ("宝塚線", "神戸線"):
            assert result[line_name]["is_unavailable"] is True
            assert result[line_name]["is_delay"] is False
            assert "取得不可" in result[line_name]["status"]
            # 取得不可の状態を「平常運転」の文言と混同しないこと
            assert "平常運転" not in result[line_name]["status"]

    def test_non_200_response_returns_unavailable(self, monkeypatch):
        fake_response = MagicMock(status_code=503)
        monkeypatch.setattr(train_service.requests, "get", MagicMock(return_value=fake_response))

        result = train_service.get_jr_traffic_status()

        assert result["宝塚線"]["is_unavailable"] is True
        assert result["神戸線"]["is_unavailable"] is True

    def test_successful_response_with_no_delay_info_marks_lines_as_normal(self, monkeypatch):
        fake_response = MagicMock(status_code=200)
        fake_response.json.return_value = {"lines": {}}
        monkeypatch.setattr(train_service.requests, "get", MagicMock(return_value=fake_response))

        result = train_service.get_jr_traffic_status()

        for line_name in ("宝塚線", "神戸線"):
            assert result[line_name]["is_unavailable"] is False
            assert result[line_name]["is_delay"] is False
            assert "平常運転" in result[line_name]["status"]

    def test_successful_response_with_delay_marks_only_that_line(self, monkeypatch):
        fake_response = MagicMock(status_code=200)
        fake_response.json.return_value = {
            "lines": {"G": {"status": "遅延", "text": "○○駅で人身事故のため遅れが発生しています"}}
        }
        monkeypatch.setattr(train_service.requests, "get", MagicMock(return_value=fake_response))

        result = train_service.get_jr_traffic_status()

        assert result["宝塚線"]["is_delay"] is True
        assert result["宝塚線"]["is_unavailable"] is False
        assert result["神戸線"]["is_delay"] is False
        assert result["神戸線"]["is_unavailable"] is False

    def test_suspension_keyword_sets_is_suspended(self, monkeypatch):
        fake_response = MagicMock(status_code=200)
        fake_response.json.return_value = {
            "lines": {"A": {"status": "運転見合わせ", "text": "運転を見合わせています"}}
        }
        monkeypatch.setattr(train_service.requests, "get", MagicMock(return_value=fake_response))

        result = train_service.get_jr_traffic_status()

        assert result["神戸線"]["is_suspended"] is True


class TestGetRouteInfo:
    """get_route_info() のスクレイピング経路。

    Yahoo!路線情報のHTMLを相手にしているため、サイト側の構造変更でセレクタが
    外れても「取得失敗」の既定値がそのまま返るだけで、例外にはならない。
    つまり壊れても静かに動き続けるので、期待する形を固定しておく。
    """

    ROUTE_HTML = """
    <html><body>
      <div id="rsltlst"><li class="el">
        <div class="time">07:10<span class="small">(45分)</span>07:55</div>
        <div class="fare">580円</div>
        <div class="transfer">1回</div>
      </li></div>
      <div class="routeDetail">
        <div class="station"><dt>伊丹</dt></div>
        <div class="transport"><div>[train]JR宝塚線</div></div>
        <div class="station"><dt>尼崎</dt></div>
        <div class="transport"><div>[train]JR京都線</div></div>
        <div class="station"><dt>長岡京</dt></div>
      </div>
    </body></html>
    """

    def _response(self, *, status=200, text="<html></html>", url="https://example.invalid/q"):
        resp = MagicMock()
        resp.status_code = status
        resp.text = text
        resp.url = url
        return resp

    def test_parses_departure_arrival_duration_fare_and_transfer(self, monkeypatch):
        monkeypatch.setattr(
            train_service.requests, "get", MagicMock(return_value=self._response(text=self.ROUTE_HTML))
        )

        result = train_service.get_route_info("伊丹(兵庫県)", "長岡京")

        assert result["summary"] == "取得成功"
        assert result["departure"] == "07:10"
        assert result["arrival"] == "07:55"
        assert result["duration"] == "45分"
        assert result["cost"] == "580円"
        assert result["transfer"] == "1回"
        assert result["label"] == "伊丹(兵庫県) → 長岡京"

    def test_builds_station_and_line_sequence(self, monkeypatch):
        monkeypatch.setattr(
            train_service.requests, "get", MagicMock(return_value=self._response(text=self.ROUTE_HTML))
        )

        details = train_service.get_route_info()["details"]

        # 始発は 🚉、途中の乗換は 🔄、終着は 🏁 で区別する
        assert details == [
            "🚉 伊丹",
            "⬇️ JR宝塚線",
            "🔄 尼崎",
            "⬇️ JR京都線",
            "🏁 長岡京",
        ]

    def test_searches_20_minutes_ahead_in_jst(self, monkeypatch):
        """Issue #592: ホストOSのTZに依存せずJSTで検索する。

        naive な datetime.now() を使っていた頃は、ホストがJST以外だと実際とは
        異なる日時で検索してしまい、誤った経路が返っていた。
        """
        import datetime as _dt

        fixed = _dt.datetime(2026, 9, 21, 7, 45, tzinfo=_dt.timezone(_dt.timedelta(hours=9)))
        monkeypatch.setattr(train_service, "get_now_jst", lambda: fixed)
        captured = {}

        def fake_get(url, params=None, timeout=None):
            captured["params"] = params
            return self._response()

        monkeypatch.setattr(train_service.requests, "get", fake_get)

        train_service.get_route_info()

        params = captured["params"]
        # 07:45 + 20分 = 08:05
        assert (params["y"], params["m"], params["d"]) == (2026, "09", "21")
        assert params["hh"] == 8
        assert (params["m1"], params["m2"]) == (0, 5)
        assert params["type"] == "1"

    def test_non_200_response_keeps_placeholder_values(self, monkeypatch):
        monkeypatch.setattr(
            train_service.requests, "get", MagicMock(return_value=self._response(status=503))
        )

        result = train_service.get_route_info()

        assert result["summary"] == "取得失敗"
        assert result["departure"] == "--:--"
        assert result["details"] == []
        # 検索URL自体は残す(手で開いて確認できるようにするため)
        assert result["url"] == "https://example.invalid/q"

    def test_unexpected_html_keeps_placeholder_values(self, monkeypatch):
        """セレクタが外れても例外にせず「取得失敗」のまま返す。"""
        monkeypatch.setattr(
            train_service.requests,
            "get",
            MagicMock(return_value=self._response(text="<html><body>模様替え</body></html>")),
        )

        result = train_service.get_route_info()

        assert result["summary"] == "取得失敗"
        assert result["arrival"] == "--:--"

    def test_network_error_is_reported_in_summary(self, monkeypatch):
        monkeypatch.setattr(
            train_service.requests, "get", MagicMock(side_effect=Exception("timeout"))
        )

        result = train_service.get_route_info()

        assert result["summary"].startswith("エラー:")
        assert "timeout" in result["summary"]
