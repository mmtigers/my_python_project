#!/usr/bin/env python3
"""SwitchBotハブの稼働時/停止時で、SwitchBot API の応答に差が出るかを確かめる確認用CLI。

Issue #881 の残件(4)。高砂のハブはラズパイと別LANのため ping できず、クラウド側の断を
検知するには API 応答の差が頼りになる。ただし「ハブ停止時に API が何を返すか」は未確認の
ため、実機で次の手順で確かめ、差が出るかを判断する(読み取り専用。コマンドは送らない)。

    cd MY_HOME_SYSTEM
    # 1. ハブが稼働中の状態で保存
    .venv/bin/python tools/check_switchbot_hub_api.py --save /tmp/hub_online.json
    # 2. ハブの電源を抜いて数分待ってから保存
    .venv/bin/python tools/check_switchbot_hub_api.py --save /tmp/hub_offline.json
    # 3. 2つを比較(差が出た項目だけ表示)
    .venv/bin/python tools/check_switchbot_hub_api.py --diff /tmp/hub_online.json /tmp/hub_offline.json

記録する内容は、`GET /v1.1/devices` に出るハブ(deviceType に "Hub" を含む機器)の項目と、
各ハブの `GET /v1.1/devices/<id>/status` の応答(HTTP ステータス・statusCode・body)。
認証情報(トークン・シークレット)は出力に含めない。

APIの呼び出し回数は1回の実行で 1 + ハブ台数 回。SwitchBot API の1日の上限(10,000回)
に対して十分小さいが、繰り返し実行はしないこと。

差が出なければ、高砂は ping もAPIも検知手段が無いため対象外のままにする(Issue #881)。
"""
import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from services import switchbot_service

REQUEST_TIMEOUT_SEC = 10
HUB_TYPE_KEYWORD = "hub"


def _get(path: str) -> dict[str, Any]:
    """APIをGETし、HTTPステータスとJSON本文をそのまま返す(失敗も記録して返す)。"""
    url = f"{config.SWITCHBOT_API_HOST}{path}"
    try:
        res = requests.get(
            url,
            headers=switchbot_service.create_switchbot_auth_headers(),
            timeout=REQUEST_TIMEOUT_SEC,
        )
    except requests.RequestException as e:
        return {"http_status": None, "error": f"{type(e).__name__}: {e}"}
    try:
        body: Any = res.json()
    except ValueError:
        body = res.text[:500]
    return {"http_status": res.status_code, "body": body}


def collect() -> dict[str, Any]:
    """ハブ一覧と各ハブのstatus応答を集める。"""
    listing = _get("/v1.1/devices")
    snapshot: dict[str, Any] = {
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "devices_list": {k: v for k, v in listing.items() if k != "body"},
        "hubs": {},
    }
    body = listing.get("body")
    if not isinstance(body, dict):
        snapshot["devices_list"]["body"] = body
        return snapshot

    snapshot["devices_list"]["statusCode"] = body.get("statusCode")
    snapshot["devices_list"]["message"] = body.get("message")
    devices = (body.get("body") or {}).get("deviceList") or []
    for dev in devices:
        if HUB_TYPE_KEYWORD not in str(dev.get("deviceType", "")).lower():
            continue
        device_id = dev.get("deviceId", "")
        snapshot["hubs"][device_id] = {
            "list_entry": dev,
            "status": _get(f"/v1.1/devices/{device_id}/status"),
        }
    return snapshot


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    # 空の dict/list は子が無く何も出力されなくなるため、値そのものを残す
    # (停止時に body や deviceList が空へ変わっても差として検出するため)。
    if isinstance(value, (dict, list)) and not value:
        return {prefix: value}
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    if isinstance(value, list):
        out = {}
        for i, v in enumerate(value):
            out.update(_flatten(v, f"{prefix}[{i}]"))
        return out
    return {prefix: value}


def diff(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """2つのスナップショットの差を「項目: A -> B」の形で返す(撮影時刻は除く)。"""
    a = _flatten({k: v for k, v in before.items() if k != "captured_at"})
    b = _flatten({k: v for k, v in after.items() if k != "captured_at"})
    lines = []
    for key in sorted(set(a) | set(b)):
        if a.get(key, "<なし>") != b.get(key, "<なし>"):
            lines.append(f"{key}: {a.get(key, '<なし>')!r} -> {b.get(key, '<なし>')!r}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--save", metavar="FILE", help="スナップショットをJSONで保存する")
    parser.add_argument("--diff", nargs=2, metavar=("BEFORE", "AFTER"), help="保存済みの2ファイルを比較する(APIは呼ばない)")
    args = parser.parse_args(argv)

    if args.diff:
        with open(args.diff[0], encoding="utf-8") as f:
            before = json.load(f)
        with open(args.diff[1], encoding="utf-8") as f:
            after = json.load(f)
        lines = diff(before, after)
        if lines:
            print("差が出た項目:")
            print("\n".join(f"  {line}" for line in lines))
        else:
            print("差はありません(この方法ではハブ停止を検知できない)。")
        return 0

    if not config.SWITCHBOT_API_TOKEN or not config.SWITCHBOT_API_SECRET:
        print("SWITCHBOT_API_TOKEN / SWITCHBOT_API_SECRET が未設定です(.env を確認)。", file=sys.stderr)
        return 1

    snapshot = collect()
    text = json.dumps(snapshot, ensure_ascii=False, indent=2)
    print(text)
    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"保存しました: {args.save}", file=sys.stderr)
    if not snapshot["hubs"]:
        print("ハブ(deviceType に 'Hub' を含む機器)が一覧に見つかりませんでした。", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
