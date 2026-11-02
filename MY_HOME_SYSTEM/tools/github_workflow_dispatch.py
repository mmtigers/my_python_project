#!/usr/bin/env python3
"""GitHub Actions のワークフローを workflow_dispatch で起動する小さなCLI。

GitHub Actions の schedule(cron)は混雑時に数時間遅れることがある(実測で 7:31 の予定が
10時台に動いた)。時刻どおりに動かしたいワークフローを、ラズパイの cron から
`run_task.sh` 経由でこのスクリプトを呼んで起動する。

起動先(リポジトリ・ワークフロー・ブランチ)とトークンは個人環境値なので `.env` に置き
(`config.GITHUB_DISPATCH_*`)、このリポジトリには書かない。ワークフローへ渡す inputs だけを
引数で受け取る。

使い方:

    python3 tools/github_workflow_dispatch.py --input key=value [--input key=value ...]

成功(HTTP 204)なら終了コード0。設定不足・通信失敗・204以外の応答では終了コード1を返し、
`run_task.sh` → `tools/notify_task_failure.py` 経由で Discord の error チャンネルへ通知される。
"""
import argparse
import os
import sys
from typing import Dict, List, Optional

import requests

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from core.logger import get_logger

logger = get_logger("github_workflow_dispatch")

API_URL_TEMPLATE: str = "https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"


def parse_inputs(pairs: List[str]) -> Dict[str, str]:
    """`key=value` の並びを inputs の辞書にする。`=` を含まない要素は ValueError。"""
    inputs: Dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"--input は key=value 形式で指定してください: {pair!r}")
        inputs[key.strip()] = value
    return inputs


def missing_settings() -> List[str]:
    """起動に必要な設定のうち未設定のものの名前。"""
    missing = []
    if not config.GITHUB_DISPATCH_TOKEN:
        missing.append("GITHUB_DISPATCH_TOKEN")
    if not config.GITHUB_DISPATCH_REPO or "/" not in config.GITHUB_DISPATCH_REPO:
        missing.append("GITHUB_DISPATCH_REPO")
    if not config.GITHUB_DISPATCH_WORKFLOW:
        missing.append("GITHUB_DISPATCH_WORKFLOW")
    return missing


def dispatch(inputs: Dict[str, str]) -> int:
    """ワークフローを起動し、HTTPステータスコードを返す(通信失敗時は0)。"""
    url = API_URL_TEMPLATE.format(
        repo=config.GITHUB_DISPATCH_REPO, workflow=config.GITHUB_DISPATCH_WORKFLOW
    )
    try:
        res = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {config.GITHUB_DISPATCH_TOKEN}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"ref": config.GITHUB_DISPATCH_REF, "inputs": inputs},
            timeout=config.GITHUB_DISPATCH_TIMEOUT_SEC,
        )
    except requests.RequestException as e:
        logger.error("ワークフローの起動に失敗しました(通信エラー): %s", type(e).__name__)
        return 0
    if res.status_code != 204:
        # 応答本文にトークンは含まれないが、念のため先頭だけ残す
        logger.error("ワークフローの起動に失敗しました: HTTP %s %s", res.status_code, res.text[:200])
    return res.status_code


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="GitHub Actions のワークフローを workflow_dispatch で起動する")
    parser.add_argument("--input", action="append", default=[], metavar="KEY=VALUE",
                        help="ワークフローへ渡す inputs(複数指定可)")
    args = parser.parse_args(argv)

    try:
        inputs = parse_inputs(args.input)
    except ValueError as e:
        logger.error("%s", e)
        return 1

    missing = missing_settings()
    if missing:
        logger.error("GitHub ワークフロー起動の設定が不足しています: %s", ", ".join(missing))
        return 1

    status = dispatch(inputs)
    if status != 204:
        return 1
    logger.info("ワークフローを起動しました: inputs=%s", inputs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
