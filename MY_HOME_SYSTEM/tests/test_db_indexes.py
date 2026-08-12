# MY_HOME_SYSTEM/tests/test_db_indexes.py
"""
init_unified_db.init_db() が高頻度書き込みテーブルに時系列インデックスを
作成することの回帰テスト (CODE_REVIEW_REPORT.md 3.2 の再発防止)。

power_usage / switchbot_meter_logs / device_records はスケジューラにより
5〜10分間隔で継続的に書き込まれ、「直近の値」を ORDER BY timestamp DESC LIMIT 1
で頻繁に読み取る。インデックスがないと将来データ量が増えた際に全件スキャンになる。
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common

EXPECTED_INDEXES = {
    "power_usage": "idx_power_usage_device_ts",
    "switchbot_meter_logs": "idx_switchbot_logs_device_ts",
    "device_records": "idx_device_records_device_ts",
}


def _index_names(cur, table: str) -> set:
    rows = cur.execute(f"PRAGMA index_list({table})").fetchall()
    return {row["name"] for row in rows}


class TestExpectedIndexesExist:
    def test_power_usage_has_device_timestamp_index(self, isolated_db):
        with common.get_db_cursor() as cur:
            assert EXPECTED_INDEXES["power_usage"] in _index_names(cur, "power_usage")

    def test_switchbot_meter_logs_has_device_timestamp_index(self, isolated_db):
        with common.get_db_cursor() as cur:
            assert EXPECTED_INDEXES["switchbot_meter_logs"] in _index_names(cur, "switchbot_meter_logs")

    def test_device_records_has_device_timestamp_index(self, isolated_db):
        with common.get_db_cursor() as cur:
            assert EXPECTED_INDEXES["device_records"] in _index_names(cur, "device_records")

    def test_index_columns_cover_device_id_and_timestamp(self, isolated_db):
        """インデックスが (device_id, timestamp) の複合であることを確認する"""
        with common.get_db_cursor() as cur:
            info = cur.execute(f"PRAGMA index_info({EXPECTED_INDEXES['power_usage']})").fetchall()
            columns = [row["name"] for row in info]
            assert columns == ["device_id", "timestamp"]
