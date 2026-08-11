# MY_HOME_SYSTEM/tests/test_camera_router.py
"""
routers/camera_router.py の _resolve_segment_path (パストラバーサル対策) のテスト。
"""
import os
import sys
import tempfile

import pytest
from fastapi import HTTPException

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from routers.camera_router import _resolve_segment_path


def test_resolve_segment_path_allows_normal_file():
    with tempfile.TemporaryDirectory() as base_dir:
        cam_dir = os.path.join(base_dir, "cam1")
        os.makedirs(cam_dir)
        target = os.path.join(cam_dir, "seg1.ts")
        with open(target, "w") as f:
            f.write("dummy")

        resolved = _resolve_segment_path(base_dir, "cam1", "seg1.ts")
        assert resolved == os.path.realpath(target)


def test_resolve_segment_path_blocks_traversal_via_camera_id():
    with tempfile.TemporaryDirectory() as base_dir:
        with pytest.raises(HTTPException) as exc_info:
            _resolve_segment_path(base_dir, "..", "secret.ts")
        assert exc_info.value.status_code == 400


def test_resolve_segment_path_blocks_traversal_via_filename():
    with tempfile.TemporaryDirectory() as base_dir:
        with pytest.raises(HTTPException) as exc_info:
            _resolve_segment_path(base_dir, "cam1", "../../etc/passwd")
        assert exc_info.value.status_code == 400
