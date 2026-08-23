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
