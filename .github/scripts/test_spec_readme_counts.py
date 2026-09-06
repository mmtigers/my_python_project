# .github/scripts/test_spec_readme_counts.py
"""docs/specifications/ 配下のREADMEに書かれた仕様書の件数が実態と一致するかの検証。

Issue #508: 件数は手で書かれており、3ディレクトリとも実態とずれていた
(記載 69/46/3 に対し実際は 68/50/11。DDD は +8 の乖離)。件数は仕様書ツリーの
規模を把握する唯一の目安であり、ずれたままでは目安として機能しない。

check_spec_drift.py は「ソース1件 ↔ 仕様書1件」の対応しか見ないため、
READMEのメタ情報(件数)はその死角にある。.env.example 整合テスト
(MY_HOME_SYSTEM/tests/test_env_example_consistency.py)や .coveragerc 整合テスト
(同 test_coveragerc.py)と同じく、機械的に固定する。

失敗したときは、エラーメッセージが示す実件数へREADMEの数字を書き換えること
(仕様書を追加・削除したPRでは、その更新も同じPRに含める)。

CI では test.yml の lint ジョブから `pytest .github/scripts/` で実行される。
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_ROOT = REPO_ROOT / "docs" / "specifications"

# (サブディレクトリ名, 件数が書かれたREADME, その中で件数を抜き出す正規表現)
# 正規表現は「数字の直前・直後の文言」で位置を特定する。README側の文言を変える
# ときはここも合わせて更新すること。
COUNT_SITES = [
    ("MY_HOME_SYSTEM", SPEC_ROOT / "README.md", r"FastAPIバックエンド\((\d+)件"),
    ("family-quest", SPEC_ROOT / "README.md", r"React/TypeScriptフロントエンド\((\d+)件"),
    ("DDD", SPEC_ROOT / "README.md", r"データ自動収集バッチ処理群\((\d+)件\)"),
    ("MY_HOME_SYSTEM", SPEC_ROOT / "MY_HOME_SYSTEM" / "README.md", r"仕様書索引（全(\d+)件）"),
    ("family-quest", SPEC_ROOT / "family-quest" / "README.md", r"格納された(\d+)件の仕様書"),
]


def _actual_count(subdir: str) -> int:
    """サブディレクトリ配下の仕様書ファイル数(README.md は索引なので除く)。"""
    return sum(
        1
        for p in (SPEC_ROOT / subdir).rglob("*.md")
        if p.name != "README.md"
    )


@pytest.mark.parametrize(
    "subdir,readme,pattern",
    COUNT_SITES,
    ids=[f"{s}:{r.parent.name}" for s, r, _ in COUNT_SITES],
)
def test_readme_spec_count_matches_actual_files(subdir, readme, pattern):
    text = readme.read_text(encoding="utf-8")
    match = re.search(pattern, text)
    assert match, (
        f"{readme.relative_to(REPO_ROOT)} から件数を抽出できなかった "
        f"(パターン: {pattern})。README側の文言を変えた場合は "
        f"COUNT_SITES の正規表現も更新すること。"
    )
    documented = int(match.group(1))
    actual = _actual_count(subdir)
    assert documented == actual, (
        f"{readme.relative_to(REPO_ROOT)} の {subdir} の件数が実態と違う: "
        f"記載 {documented}件 / 実際 {actual}件。READMEの数字を {actual} に更新すること。"
    )
