# .github/scripts/test_crontab_keep_alive.py
"""Issue #585 / #664、および 2026-09-19 の「BT運用休止で確定」判断の回帰テスト。

経緯:

- **#585**: `MY_HOME_SYSTEM/tools/keep_alive_anker.sh`・`keep_alive_speaker.sh`は
  いずれも「cron実行時でもPipeWireソケットを見つけられるように」という前提の
  コメントを持つ(=cron起動を前提に書かれている)にも関わらず、
  `deploy/cron/crontab`・`MY_HOME_SYSTEM/deploy/systemd/`のいずれにも実行契機が
  無かった。実行契機皆無の状態を解消するため、当時は**両方を**crontabへ登録した。
- **#664**: 2スクリプトを比較のうえ`keep_alive_anker.sh`へ一本化し、
  `keep_alive_speaker.sh`はスクリプトごと削除した(判断理由は
  `deploy/cron/README.md`と削除コミットのメッセージを参照)。
- **2026-09-19(実機棚卸し)**: `config.ENABLE_BLUETOOTH`が既定`False`・実機`.env`の
  `SPEAKER_BLUETOOTH_MAC`も未設定で、5分毎の実行が一度も鳴動せずno-opで正常終了し
  続けていた(=BTスピーカー運用そのものが休止中)。「休止で確定」と判断し、
  crontabのエントリを外した。スクリプトは再有効化に必要な資産として残している。

本テストは「`ENABLE_BLUETOOTH`とcron登録の対応関係」を固定する。すなわち
BT運用が無効の間はキープアライブをcronへ登録しないこと、有効化したときは
登録されていること、そして登録するなら実体のあるスクリプトを指していること。

実行間隔そのものの妥当性(実機のBluetoothデバイスの実際のオートオフ時間)は
このリポジトリのみからは確認できないため対象外。

CI では test.yml の lint ジョブから `pytest .github/scripts/` で実行される。
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CRONTAB = REPO_ROOT / "deploy" / "cron" / "crontab"
CONFIG_PY = REPO_ROOT / "MY_HOME_SYSTEM" / "config.py"
ANKER_SCRIPT = REPO_ROOT / "MY_HOME_SYSTEM" / "tools" / "keep_alive_anker.sh"
RETIRED_SPEAKER_SCRIPT = REPO_ROOT / "MY_HOME_SYSTEM" / "tools" / "keep_alive_speaker.sh"


def _crontab_lines():
    return [
        line for line in CRONTAB.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _entries_for(script_name: str):
    return [line for line in _crontab_lines() if script_name in line]


def _bluetooth_enabled() -> bool:
    """config.py の ENABLE_BLUETOOTH の既定値を読む(configのimportは避ける)。

    `.env`(実機のみに存在)には依存させない。リポジトリ上の既定値が正。
    """
    m = re.search(r"^ENABLE_BLUETOOTH:\s*bool\s*=\s*(True|False)\s*$",
                  CONFIG_PY.read_text(encoding="utf-8"), re.MULTILINE)
    assert m, "config.py の ENABLE_BLUETOOTH の既定値を読み取れませんでした"
    return m.group(1) == "True"


def test_keep_alive_registration_matches_bluetooth_flag():
    """BT運用の有効/無効と、キープアライブのcron登録の有無が一致すること。"""
    entries = _entries_for("keep_alive_anker.sh")
    if _bluetooth_enabled():
        assert len(entries) == 1, (
            "config.ENABLE_BLUETOOTH=True なら keep_alive_anker.sh のcronエントリが"
            f"1件必要です(現在{len(entries)}件)。#585 の「実行契機が皆無」の再発を防ぐため"
        )
        assert "MY_HOME_SYSTEM/tools/keep_alive_anker.sh" in entries[0]
    else:
        assert not entries, (
            "config.ENABLE_BLUETOOTH=False の間は5分毎の実行がno-opになるだけなので"
            "cronへ登録しない(2026-09-19の判断)。運用を再開するなら ENABLE_BLUETOOTH と"
            " .env の SPEAKER_BLUETOOTH_MAC を先に設定すること。手順は deploy/cron/crontab の"
            "該当コメントにある"
        )


def test_keep_alive_anker_script_is_kept_for_reactivation():
    """休止解除に必要なスクリプトの実体が残っていること。

    `config.py` の ENABLE_BLUETOOTH のコメントが再有効化手順として
    `tools/connect_speaker.sh` の定期実行の整備を挙げており、
    キープアライブ側のスクリプトもその一部として保持している。
    実体の無いパスをcronへ登録しても、cronはシェルの起動失敗を通知しないため
    無言で毎回失敗し続ける(#587で同種の失敗が実際に起きている)。
    """
    assert ANKER_SCRIPT.is_file()


def test_retired_keep_alive_speaker_is_gone():
    """#664: `keep_alive_speaker.sh`はスクリプトごと削除済み。

    crontab側にだけエントリが残る/復活すると、存在しないスクリプトを
    起動しようとして無言で失敗し続ける状態になる。
    """
    assert not RETIRED_SPEAKER_SCRIPT.exists(), (
        "keep_alive_speaker.sh は #664 で keep_alive_anker.sh へ一本化して削除した。"
        "復活させる場合は deploy/cron/README.md の判断理由も更新すること"
    )
    assert not _entries_for("keep_alive_speaker.sh"), (
        "削除済みの keep_alive_speaker.sh が crontab に登録されている"
    )
