# .github/scripts/test_systemd_units.py
"""`MY_HOME_SYSTEM/deploy/systemd/*.service` の回帰テスト(AUDIT-002)。

経緯: `home_system.service` の `StartLimitIntervalSec=300` / `StartLimitBurst=5`
(クラッシュループの歯止め)が `[Service]` セクションに置かれていた。これらは
systemd v229/v230 以降 `[Unit]` セクションのディレクティブで、**新名**の
`StartLimitIntervalSec=` を `[Service]` に書くと解釈されない。実機(Raspberry Pi /
systemd 257)で `systemd-analyze verify` が

    Unknown key 'StartLimitIntervalSec' in section [Service], ignoring.

を出すことを確認済みで、ユニットのコメントが「5 分間に 5 回まで」と断定していた
歯止めは実際には成立しておらず、既定の `DefaultStartLimitIntervalSec`(10 秒)が
適用されていた。

`systemd-analyze verify` はこの誤配置を検知しても終了コード 0 を返すため
CI のゲートにできない(実機で確認済み)。またそもそも ExecStart のパスは
ラズパイ固有で CI ランナーには存在しない。よってセクション配置だけを
静的に検査する。

CI では test.yml の lint ジョブから `pytest .github/scripts/` で実行される。
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEMD_DIR = REPO_ROOT / "MY_HOME_SYSTEM" / "deploy" / "systemd"

# systemd v229/v230 以降 [Unit] セクションでのみ有効なディレクティブ。
UNIT_ONLY_KEYS = ("StartLimitIntervalSec", "StartLimitBurst", "StartLimitAction")


def _sections(unit_path: Path) -> dict[str, list[str]]:
    """ユニットファイルを {セクション名: [キー, ...]} に分解する。"""
    sections: dict[str, list[str]] = {}
    current = ""
    for raw in unit_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, [])
            continue
        if "=" in line:
            sections.setdefault(current, []).append(line.split("=", 1)[0].strip())
    return sections


def _unit_files() -> list[Path]:
    units = sorted(SYSTEMD_DIR.glob("*.service"))
    assert units, f"{SYSTEMD_DIR} に .service が見つかりません"
    return units


def test_start_limit_keys_are_not_in_service_section():
    """StartLimit* が [Service] に書かれていないこと(書くと無言で無視される)。"""
    misplaced = []
    for unit in _unit_files():
        service_keys = _sections(unit).get("Service", [])
        for key in UNIT_ONLY_KEYS:
            if key in service_keys:
                misplaced.append(f"{unit.name}: [Service] の {key}")
    assert not misplaced, (
        "StartLimit* は [Unit] セクションのディレクティブで、[Service] に書くと "
        "systemd に無視される(歯止めが効かない): " + ", ".join(misplaced)
    )


def test_home_system_service_keeps_crash_loop_guard():
    """自動復旧(Restart=)を持つ home_system.service に歯止めが残っていること。"""
    sections = _sections(SYSTEMD_DIR / "home_system.service")
    unit_keys = sections.get("Unit", [])
    assert "Restart" in sections.get("Service", []), (
        "Restart= が無くなっている。Issue #646 の自動復旧が失われていないか確認すること"
    )
    for key in ("StartLimitIntervalSec", "StartLimitBurst"):
        assert key in unit_keys, f"[Unit] の {key} が無い(クラッシュループの歯止め)"
