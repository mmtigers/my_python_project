# .github/scripts/test_crontab_keep_alive.py
"""Issue #585 / #664 の回帰テスト。

経緯:

- **#585**: `MY_HOME_SYSTEM/tools/keep_alive_anker.sh`・`keep_alive_speaker.sh`は
  いずれも「cron実行時でもPipeWireソケットを見つけられるように」という前提の
  コメントを持つ(=cron起動を前提に書かれている)にも関わらず、
  `deploy/cron/crontab`・`MY_HOME_SYSTEM/deploy/systemd/`のいずれにも実行契機が
  無かった。実行契機皆無の状態を解消するため、当時は**両方を**crontabへ登録した。
- **#664**: 上記は「どちらが必要か判断がついていない」ことの暫定対応であり、
  `deploy/cron/README.md`自身が「両方が実際に必要かどうかも…確認・整理される
  余地がある」と記述していた。2スクリプトを比較のうえ`keep_alive_anker.sh`へ
  一本化し、`keep_alive_speaker.sh`はスクリプトごと削除した(判断理由は
  `deploy/cron/README.md`と削除コミットのメッセージを参照)。

本テストは一本化後の状態を固定する。すなわち`keep_alive_anker.sh`のcron
エントリが1件だけ存在すること、および削除した`keep_alive_speaker.sh`が
crontabへ復活していないこと(スクリプト実体が無いまま登録されると、cronは
毎回無言で失敗し続ける)を検査する。

実行間隔そのものの妥当性(実機のBluetoothデバイスの実際のオートオフ時間)は
このリポジトリのみからは確認できないため対象外。

CI では test.yml の lint ジョブから `pytest .github/scripts/` で実行される。
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CRONTAB = REPO_ROOT / "deploy" / "cron" / "crontab"
ANKER_SCRIPT = REPO_ROOT / "MY_HOME_SYSTEM" / "tools" / "keep_alive_anker.sh"
RETIRED_SPEAKER_SCRIPT = REPO_ROOT / "MY_HOME_SYSTEM" / "tools" / "keep_alive_speaker.sh"


def _crontab_lines():
    return [
        line for line in CRONTAB.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _entry_for(script_name: str) -> str:
    matches = [line for line in _crontab_lines() if script_name in line]
    assert len(matches) == 1, (
        f"{script_name} を起動するcronエントリが1件のはずですが{len(matches)}件見つかりました: {matches}"
    )
    return matches[0]


def test_keep_alive_anker_is_registered_in_crontab():
    anker_line = _entry_for("keep_alive_anker.sh")

    assert "MY_HOME_SYSTEM/tools/keep_alive_anker.sh" in anker_line


def test_keep_alive_anker_script_exists():
    """crontabに登録したスクリプトの実体がリポジトリに存在すること。

    実体の無いパスをcronへ登録しても、cronはシェルの起動失敗を通知しないため
    無言で毎回失敗し続ける(#587で同種の失敗が実際に起きている)。
    """
    assert ANKER_SCRIPT.is_file()


def test_retired_keep_alive_speaker_is_gone():
    """#664: `keep_alive_speaker.sh`はスクリプトごと削除済み。

    crontab側にだけエントリが残る/復活すると、存在しないスクリプトを5分毎に
    起動しようとして無言で失敗し続ける状態になる。
    """
    assert not RETIRED_SPEAKER_SCRIPT.exists(), (
        "keep_alive_speaker.sh は #664 で keep_alive_anker.sh へ一本化して削除した。"
        "復活させる場合は deploy/cron/README.md の判断理由も更新すること"
    )
    assert not [line for line in _crontab_lines() if "keep_alive_speaker.sh" in line], (
        "削除済みの keep_alive_speaker.sh が crontab に登録されている"
    )
