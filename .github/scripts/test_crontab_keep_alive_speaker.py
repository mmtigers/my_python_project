# .github/scripts/test_crontab_keep_alive_speaker.py
"""Issue #585の回帰テスト。

`MY_HOME_SYSTEM/tools/keep_alive_anker.sh`・`keep_alive_speaker.sh`はいずれも
「cron実行時でもPipeWireソケットを見つけられるように」という前提のコメントを
持つ(=cron起動を前提に書かれている)にも関わらず、`deploy/cron/crontab`・
`MY_HOME_SYSTEM/deploy/systemd/`のいずれにも実行契機が無く、実質的に一度も
実行されていなかった可能性があった。両スクリプトをcrontabへ登録し、
Bluetoothスピーカーのオートパワーオフを防ぐのに十分な頻度(5分毎)で
実行されるようにした。

本テストは、両エントリがcrontabに存在し、互いに(同一分での実行による
PulseAudio/PipeWireクライアントの衝突を避けるため)異なる時刻に実行される
ことを固定する。実行間隔そのものの妥当性(実機のBluetoothデバイスの
実際のオートオフ時間)はこのリポジトリのみからは確認できないため対象外。

CI では test.yml の lint ジョブから `pytest .github/scripts/` で実行される。
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CRONTAB = REPO_ROOT / "deploy" / "cron" / "crontab"


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


def test_both_keep_alive_scripts_are_registered_in_crontab():
    anker_line = _entry_for("keep_alive_anker.sh")
    speaker_line = _entry_for("keep_alive_speaker.sh")

    assert "MY_HOME_SYSTEM/tools/keep_alive_anker.sh" in anker_line
    assert "MY_HOME_SYSTEM/tools/keep_alive_speaker.sh" in speaker_line


def test_keep_alive_entries_do_not_share_the_same_minute_field():
    """同一分に両方が起動すると、同じPulseAudio/PipeWireクライアントへ同時に
    アクセスしうるため、時刻(分フィールド)をずらして登録すること。"""
    anker_minute_field = _entry_for("keep_alive_anker.sh").split()[0]
    speaker_minute_field = _entry_for("keep_alive_speaker.sh").split()[0]

    assert anker_minute_field != speaker_minute_field
