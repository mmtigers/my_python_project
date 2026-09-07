#!/usr/bin/env python3
"""カバレッジのラチェットチェック(PR の計測値を master の直近成功 run と比較する)。

背景: test.yml の `--cov-fail-under` は固定値で、実測が閾値より数ポイント高い期間は
退行を見逃す。これまでは人手で「実測より約3pt下」へ段階的に引き上げていた
(64 → 67 → 70。Issue #494 / #536)。この手作業を無くし、かつ CI を不必要に赤く
しないために、固定閾値(床)はそのまま残し、加えて **master の直近成功 run の
計測値から tolerance を超えて下がったら失敗** させる(ラチェット)。閾値の引き上げ
作業は不要になり、床の値はドキュメントに書かれた最低保証として据え置ける。

比較元(baseline)は test.yml が `gh run download` で master の成果物
(coverage-report / frontend-coverage-report)から取り出したファイル。取り出せない
場合(成果物の保持期限切れ・master に成功 run が無い等)は比較をスキップし、
固定閾値だけがゲートになる(このスクリプトは失敗させない)。

対応フォーマット:
  - Cobertura XML (pytest-cov の --cov-report=xml): ルート要素の line-rate 属性
  - vitest の json-summary (coverage/coverage-summary.json): total.lines.pct

使い方:
  check_coverage_ratchet.py --label backend \\
      --current MY_HOME_SYSTEM/coverage.xml \\
      --baseline baseline/MY_HOME_SYSTEM/coverage.xml [--tolerance 0.5]

終了コード: 0 = 許容範囲内 or 比較スキップ、1 = tolerance を超える低下、2 = 引数/入力エラー
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

DEFAULT_TOLERANCE = 0.5


def read_line_coverage_percent(path: Path) -> float:
    """カバレッジファイルから行カバレッジ(%)を読む。拡張子で形式を判定する。"""
    if path.suffix.lower() == ".xml":
        root = ET.parse(path).getroot()
        rate = root.attrib.get("line-rate")
        if rate is None:
            raise ValueError(f"{path}: Cobertura XML のルート要素に line-rate 属性がありません")
        return float(rate) * 100.0
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        try:
            return float(data["total"]["lines"]["pct"])
        except (KeyError, TypeError) as e:
            raise ValueError(f"{path}: json-summary に total.lines.pct がありません") from e
    raise ValueError(f"{path}: 対応していない形式です(.xml / .json のみ)")


def compare(
    current: float, baseline: Optional[float], tolerance: float, label: str
) -> tuple[int, str]:
    """(終了コード, 表示メッセージ) を返す。baseline が None なら比較スキップ。"""
    if baseline is None:
        return 0, (
            f"[coverage-ratchet] {label}: 比較元(master)のカバレッジが取得できないため、"
            f"ラチェット比較をスキップします(固定閾値のみ適用)。現在値 {current:.2f}%"
        )
    delta = current - baseline
    if delta < -tolerance:
        return 1, (
            f"::error::[coverage-ratchet] {label}: カバレッジが master 比で {-delta:.2f}pt 低下しました "
            f"(master {baseline:.2f}% → PR {current:.2f}%、許容 {tolerance:.2f}pt)。"
            "追加・変更したコードにテストを足すか、低下が意図的なら PR 本文にその理由を書いてください。"
        )
    return 0, (
        f"[coverage-ratchet] {label}: OK (master {baseline:.2f}% → PR {current:.2f}%、"
        f"差 {delta:+.2f}pt、許容 -{tolerance:.2f}pt)"
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", default="coverage", help="表示用ラベル(backend / frontend 等)")
    parser.add_argument("--current", required=True, type=Path, help="PR 側のカバレッジファイル")
    parser.add_argument("--baseline", required=True, type=Path,
                        help="master 側のカバレッジファイル(存在しなければ比較スキップ)")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                        help=f"許容する低下幅(pt)。既定 {DEFAULT_TOLERANCE}")
    args = parser.parse_args(argv)

    try:
        current = read_line_coverage_percent(args.current)
    except (OSError, ValueError, ET.ParseError) as e:
        print(f"::error::[coverage-ratchet] {args.label}: PR 側のカバレッジを読めません: {e}")
        return 2

    baseline: Optional[float] = None
    if args.baseline.is_file():
        try:
            baseline = read_line_coverage_percent(args.baseline)
        except (ValueError, ET.ParseError) as e:
            # 比較元が壊れていても PR を赤くはしない(固定閾値だけがゲート)
            print(f"::warning::[coverage-ratchet] {args.label}: 比較元を読めないためスキップ: {e}")
            baseline = None

    code, message = compare(current, baseline, args.tolerance, args.label)
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
