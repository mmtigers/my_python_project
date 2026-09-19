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

# [Service] セクション専用のディレクティブ([Unit] に書いても無視される)。
# Issue #731: 逆向きの取り違えも同じ「無言で無視される」失敗モードになるため、
# 両方向を検査する。
SERVICE_ONLY_KEYS = (
    "Type", "ExecStart", "ExecStartPre", "ExecStop", "Restart", "RestartSec",
    "TimeoutStartSec", "TimeoutStopSec", "KillMode", "RemainAfterExit",
    "WorkingDirectory", "User", "Group", "Environment", "EnvironmentFile",
    "StandardOutput", "StandardError",
)

# 同一セクション内での重複指定が正当な意味を持つディレクティブ。
REPEATABLE_KEYS = frozenset({"Environment", "ExecStartPre", "ExecStopPost", "ExecReload"})


def _entries(unit_path: Path) -> dict[str, list[tuple[int, str, str]]]:
    """ユニットファイルを {セクション名: [(行番号, キー, 値), ...]} に分解する。"""
    entries: dict[str, list[tuple[int, str, str]]] = {}
    current = ""
    for lineno, raw in enumerate(unit_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            entries.setdefault(current, [])
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            entries.setdefault(current, []).append((lineno, key.strip(), value.strip()))
    return entries


def _sections(unit_path: Path) -> dict[str, list[str]]:
    """{セクション名: [キー, ...]}(キーだけを見る検査用の薄いビュー)。"""
    return {
        section: [key for _lineno, key, _value in items]
        for section, items in _entries(unit_path).items()
    }


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


def test_service_only_keys_are_not_in_unit_section():
    """[Service] 専用のディレクティブが [Unit] に紛れていないこと(Issue #731)。

    systemd は取り違えをエラーにせず「Unknown key ... ignoring」を journal に
    出して黙って無視するため、設定したつもりの挙動が効かない状態が長期間
    気づかれない。AUDIT-002 がまさにその形で見つかった。
    """
    misplaced = []
    for unit in _unit_files():
        unit_keys = _sections(unit).get("Unit", [])
        for key in SERVICE_ONLY_KEYS:
            if key in unit_keys:
                misplaced.append(f"{unit.name}: [Unit] の {key}")
    assert not misplaced, (
        "これらは [Service] セクションのディレクティブで、[Unit] に書くと無視される: "
        + ", ".join(misplaced)
    )


def test_no_duplicate_keys_within_a_section():
    """同一セクション内でキーが重複していないこと(後勝ちで意図と違う値が効く)。"""
    duplicates = []
    for unit in _unit_files():
        for section, items in _entries(unit).items():
            seen: dict[str, int] = {}
            for lineno, key, _value in items:
                if key in REPEATABLE_KEYS:
                    continue
                if key in seen:
                    duplicates.append(f"{unit.name}:[{section}] {key}= が {seen[key]} 行目と {lineno} 行目に重複")
                seen[key] = lineno
    assert not duplicates, "\n".join(duplicates)


def test_home_system_service_has_a_start_timeout_for_its_heavy_execstartpre():
    """Issue #731 (AUDIT-001): ExecStartPre が既定の90秒で kill されないこと。

    ExecStartPre の start_all.sh --prepare は NAS マウント待ち(最大31秒)・
    requirements.txt 変更時の pip install(117パッケージ)・family-quest の再ビルド
    (npm ci + tsc -b && vite build)を含み、ラズパイでは数分かかる。start ジョブの
    タイムアウトは ExecStartPre の完走までを含むため、未指定だと
    DefaultTimeoutStartSec(通常90秒)で SIGTERM → Restart=on-failure で再試行、
    という起動ループに陥る。
    """
    entries = _entries(SYSTEMD_DIR / "home_system.service")
    service = entries.get("Service", [])

    pres = [value for _lineno, key, value in service if key == "ExecStartPre"]
    assert any(v.endswith("start_all.sh --prepare") for v in pres), (
        "重い前処理(ExecStartPre)が無くなっている。TimeoutStartSec を置く理由が"
        "残っているか確認すること"
    )

    timeouts = [value for _lineno, key, value in service if key == "TimeoutStartSec"]
    assert timeouts, "TimeoutStartSec が無いと DefaultTimeoutStartSec(90秒)で kill される"
    assert int(timeouts[0]) >= 600, "ExecStartPre の実測(数分)に対して余裕が足りない"
