# MY_HOME_SYSTEM/core/onvif_utils.py
"""ONVIF 関連の小さな共通ユーティリティ。

Issue #661: `find_wsdl_path()` が `services/camera_service.py` と
`monitors/camera_monitor.py` に一字一句同じ形で重複しており(片方の docstring は
「camera_monitor.py と同等の」と書いてあった)、探索規則を変えるときに片方だけ
直す事故が起きうる状態だった。実体をここへ移し、両者はこれを import する。
"""
from __future__ import annotations

import os
import sys
from typing import Optional


def find_wsdl_path() -> Optional[str]:
    """WSDLファイルのディレクトリを動的に探索する。

    `onvif_zeep` の配置は環境(venv / システム Python / パッケージのバージョン)で
    `<site-packages>/onvif/wsdl` だったり `<site-packages>/wsdl` だったりするため、
    `sys.path` を走査して `devicemgmt.wsdl` が実在する方を返す。見つからなければ None。
    """
    for path in sys.path:
        if not os.path.exists(path):
            continue
        candidate_standard = os.path.join(path, 'onvif', 'wsdl')
        candidate_direct = os.path.join(path, 'wsdl')
        for candidate in [candidate_standard, candidate_direct]:
            if os.path.exists(os.path.join(candidate, 'devicemgmt.wsdl')):
                return candidate
    return None
