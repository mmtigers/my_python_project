"""deploy/systemd/*.service の静的検証。

Issue #731 / #732(REPOSITORY_AUDIT_2026-09-18 の AUDIT-001 / AUDIT-002):
リポジトリには ruff(Python)・ESLint(TypeScript)・shellcheck(シェル)があるのに、
本番の起動を担う systemd ユニットだけは**誰にも検証されていなかった**。その結果
`StartLimitIntervalSec=` / `StartLimitBurst=` が [Service] に置かれ(systemd v229/v230 以降は
[Unit] のディレクティブ)、コメントが断定していた「5 分間に 5 回まで」の歯止めが
実機で成立していなかった(AUDIT-002)。

`systemd-analyze verify` は CI(ubuntu-latest)では ExecStart のパス
(/home/masahiro/develop/...)が存在せず必ず失敗するため使えない。代わりに
「どのディレクティブがどのセクションに属するか」だけを自前で検証する。
"""

import os

import pytest

SYSTEMD_DIR = os.path.join(os.path.dirname(__file__), "..", "deploy", "systemd")

# systemd v229/v230 以降 [Unit] セクションのディレクティブ。[Service] に書いても
# 新名(...Sec=)は解釈されず黙って無視される。
UNIT_ONLY_DIRECTIVES = {
    "StartLimitIntervalSec",
    "StartLimitInterval",
    "StartLimitBurst",
    "StartLimitAction",
    "RequiresMountsFor",
    "After",
    "Before",
    "Requires",
    "Wants",
    "Description",
    "ConditionPathExists",
}

# [Service] セクション専用のディレクティブ。[Unit] に書いても無視される。
SERVICE_ONLY_DIRECTIVES = {
    "Type",
    "ExecStart",
    "ExecStartPre",
    "ExecStop",
    "Restart",
    "RestartSec",
    "TimeoutStartSec",
    "TimeoutStopSec",
    "KillMode",
    "RemainAfterExit",
    "WorkingDirectory",
    "User",
    "Group",
    "Environment",
    "EnvironmentFile",
    "StandardOutput",
    "StandardError",
}


def _unit_files():
    return sorted(
        os.path.join(SYSTEMD_DIR, name)
        for name in os.listdir(SYSTEMD_DIR)
        if name.endswith(".service")
    )


def _parse(path):
    """[セクション名] -> [(行番号, キー, 値), ...] に分解する(コメント・空行は捨てる)。"""
    sections: dict[str, list[tuple[int, str, str]]] = {}
    current = None
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1]
                sections.setdefault(current, [])
                continue
            if current is None or "=" not in line:
                continue
            key, _, value = line.partition("=")
            sections[current].append((lineno, key.strip(), value.strip()))
    return sections


@pytest.mark.parametrize("path", _unit_files(), ids=os.path.basename)
def test_directives_are_in_the_correct_section(path):
    """[Unit] 専用/[Service] 専用のディレクティブが取り違えられていないこと。

    取り違えても systemd はエラーにせず「Unknown key ... ignoring」を journal に出して
    黙って無視するため、設定したつもりの防御が効かない状態が長期間気づかれない。
    """
    sections = _parse(path)
    misplaced = []
    for section, entries in sections.items():
        for lineno, key, _value in entries:
            if section == "Service" and key in UNIT_ONLY_DIRECTIVES:
                misplaced.append(f"{os.path.basename(path)}:{lineno} {key}= は [Unit] に書く")
            if section == "Unit" and key in SERVICE_ONLY_DIRECTIVES:
                misplaced.append(f"{os.path.basename(path)}:{lineno} {key}= は [Service] に書く")
    assert not misplaced, "セクションを取り違えたディレクティブがある:\n" + "\n".join(misplaced)


@pytest.mark.parametrize("path", _unit_files(), ids=os.path.basename)
def test_no_duplicate_keys_within_a_section(path):
    """同一セクション内でのキーの重複が無いこと(後勝ちで意図と異なる値が効く事故を防ぐ)。"""
    duplicates = []
    for section, entries in _parse(path).items():
        seen: dict[str, int] = {}
        for lineno, key, _value in entries:
            # Environment= / ExecStartPre= は複数指定が正当な意味を持つ
            if key in {"Environment", "ExecStartPre", "ExecStopPost", "ExecReload"}:
                continue
            if key in seen:
                duplicates.append(
                    f"{os.path.basename(path)}:[{section}] {key}= が {seen[key]} 行目と {lineno} 行目に重複"
                )
            seen[key] = lineno
    assert not duplicates, "\n".join(duplicates)


class TestHomeSystemService:
    """本体ユニット固有の不変条件。"""

    @property
    def sections(self):
        return _parse(os.path.join(SYSTEMD_DIR, "home_system.service"))

    def _service_value(self, key):
        for _lineno, k, value in self.sections.get("Service", []):
            if k == key:
                return value
        return None

    def _unit_value(self, key):
        for _lineno, k, value in self.sections.get("Unit", []):
            if k == key:
                return value
        return None

    def test_start_limit_is_in_the_unit_section(self):
        """AUDIT-002: クラッシュループの歯止めが実際に解釈される位置にあること。"""
        assert self._unit_value("StartLimitIntervalSec") == "300"
        assert self._unit_value("StartLimitBurst") == "5"

    def test_timeout_start_sec_covers_the_heavy_execstartpre(self):
        """AUDIT-001: ExecStartPre(NAS 待ち・pip install・フロント再ビルド)は既定の 90 秒を
        超えうるため、TimeoutStartSec を明示して起動ループを防ぐこと。"""
        value = self._service_value("TimeoutStartSec")
        assert value is not None, "TimeoutStartSec が無いと DefaultTimeoutStartSec(90秒)で kill される"
        assert int(value) >= 600, "ExecStartPre の実測(数分)に対して余裕が足りない"

    def test_execstartpre_exists_so_the_timeout_is_meaningful(self):
        """TimeoutStartSec を置く理由(重い前処理)が残っていること。"""
        pres = [v for _l, k, v in self.sections.get("Service", []) if k == "ExecStartPre"]
        assert any(v.endswith("start_all.sh --prepare") for v in pres)
