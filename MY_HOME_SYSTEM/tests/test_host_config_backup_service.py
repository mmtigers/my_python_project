# MY_HOME_SYSTEM/tests/test_host_config_backup_service.py
"""ホスト設定バックアップ (Issue #774) の回帰テスト。

このテストが固定する最重要の性質は**秘密を NAS へ書かないこと**である。
バックアップは「失われないようにする」仕組みなので、うっかり平文の認証情報を
664 で見える NAS 共有へ置くと、バックアップを増やしたことで秘密の露出面が
増えるという逆効果になる。#649(`.env` を対象外にした)・#773(RTSP 認証情報を
リポジトリに含めなかった)と同じ判断をコードで固定する。

実機の `/etc` は読めないため、`root=` で擬似ルートを差し替えて検証する。
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.utils import get_now_jst
from services import host_config_backup_service as svc


def _jst(year, month, day, hour=0):
    """JST の aware な datetime。`prune` は `get_now_jst()` と同じ aware な値を
    受け取る前提なので、テストでも naive な datetime を渡さない。"""
    return get_now_jst().replace(
        year=year, month=month, day=day, hour=hour,
        minute=0, second=0, microsecond=0,
    )


@pytest.fixture
def fake_host(tmp_path, monkeypatch):
    """`/etc` 相当を tmp_path 配下に作り、対象宣言をその構成に合わせる。"""
    etc = tmp_path / "host" / "etc"
    (etc / "systemd" / "system").mkdir(parents=True)
    (etc / "nvr").mkdir(parents=True)
    (etc / "samba").mkdir(parents=True)

    (etc / "systemd" / "system" / "nvr-entrance.service").write_text(
        "[Unit]\nDescription=NVR entrance\n[Service]\nEnvironmentFile=/etc/nvr/entrance.env\n",
        encoding="utf-8",
    )
    # 秘密: 中身がコピーされてはならない
    (etc / "nvr" / "entrance.env").write_text(
        "RTSP_URL=rtsp://admin:SUPERSECRET@192.168.1.50/stream\n", encoding="utf-8"
    )
    (etc / "samba" / "smbcredentials").write_text(
        "username=nasuser\npassword=ALSOSECRET\n", encoding="utf-8"
    )
    # 宣言上は秘密でないが、実機の中身は環境によって違う(fstab に直書きもありうる)
    (etc / "fstab").write_text(
        "//nas/home_system /mnt/nas cifs "
        "credentials=/etc/samba/smbcredentials,noserverino,vers=3.0 0 0\n"
        "//nas/other /mnt/other cifs username=u,password=INLINE_SECRET,noserverino 0 0\n",
        encoding="utf-8",
    )
    return tmp_path / "host"


@pytest.fixture
def targets(monkeypatch):
    """テスト用の対象宣言(擬似ルートに対する相対構成は本番と同じ絶対パス)。"""
    monkeypatch.setattr(svc, "HOST_CONFIG_TARGETS", (
        svc.HostConfigTarget("/etc/systemd/system/nvr-*.service", "常時録画", repo_managed=True),
        svc.HostConfigTarget("/etc/fstab", "マウント定義"),
        svc.HostConfigTarget("/etc/samba/smbcredentials", "NAS 認証情報", secret=True),
        svc.HostConfigTarget("/etc/nvr/*.env", "RTSP 認証情報", secret=True),
        svc.HostConfigTarget("/etc/cloudflared/config.yml", "cloudflared(不在)"),
    ))


def _run(tmp_path, fake_host, **kw):
    dest_root = tmp_path / "nas" / "host_config_backups"
    return svc.perform_backup(dest_root=str(dest_root), root=str(fake_host), **kw), dest_root


class TestSecretsAreNeverWritten:
    def test_secret_file_contents_are_not_copied(self, tmp_path, fake_host, targets):
        _, dest_root = _run(tmp_path, fake_host)

        # 出力ツリー全体を走査して、秘密の値が1度も現れないこと
        leaked = []
        for root, _dirs, files in os.walk(dest_root):
            for name in files:
                body = os.path.join(root, name)
                text = Path(body).read_text(encoding="utf-8", errors="replace")
                for secret in ("SUPERSECRET", "ALSOSECRET", "INLINE_SECRET"):
                    if secret in text:
                        leaked.append((body, secret))
        assert leaked == [], f"秘密が出力に含まれている: {leaked}"

    def test_secret_files_are_recorded_as_manifest_only(self, tmp_path, fake_host, targets):
        outcome, _ = _run(tmp_path, fake_host)
        secret_records = {r.path: r for r in outcome.records if r.secret and r.status != "missing"}
        assert secret_records, "秘密ファイルが1件も拾えていない(テストの前提が壊れている)"
        for path, record in secret_records.items():
            assert record.status == "manifest_only", path
            # 復元に必要なメタデータは残すこと
            assert record.sha256 and record.mode and record.owner, path

    def test_secret_files_have_no_copy_on_disk(self, tmp_path, fake_host, targets):
        outcome, _ = _run(tmp_path, fake_host)
        files_dir = os.path.join(outcome.dest, "files")
        assert not os.path.exists(os.path.join(files_dir, "etc", "nvr", "entrance.env"))
        assert not os.path.exists(os.path.join(files_dir, "etc", "samba", "smbcredentials"))


class TestNonSecretFilesAreRedactedAnyway:
    def test_inline_password_in_fstab_is_redacted(self, tmp_path, fake_host, targets):
        """宣言上「秘密でない」ファイルも素通しにしない。

        `/etc/fstab` に CIFS の password を直書きしている構成もありうるが、
        コードからは実機の中身を確認できないため、コピーする全ファイルに
        redact を通す。
        """
        outcome, _ = _run(tmp_path, fake_host)
        body = Path(outcome.dest, "files", "etc", "fstab").read_text(encoding="utf-8")
        assert "INLINE_SECRET" not in body
        assert svc._REDACTED in body
        # 復元に必要な非秘密の情報は残っていること
        assert "noserverino" in body
        assert "credentials=/etc/samba/smbcredentials" in body
        assert "username=u" in body  # ユーザー名は意図的に redact しない

    def test_redaction_count_is_recorded(self, tmp_path, fake_host, targets):
        """素通しだったのか redact したのかが台帳から分かること。"""
        outcome, _ = _run(tmp_path, fake_host)
        fstab = next(r for r in outcome.records if r.path.endswith("/etc/fstab"))
        assert fstab.redactions == 1
        unit = next(r for r in outcome.records if r.path.endswith("nvr-entrance.service"))
        assert unit.redactions == 0


class TestRedactSecrets:
    @pytest.mark.parametrize("line,expected_hits", [
        ("password=hunter2", 1),
        ("password: hunter2", 1),
        ("api_key=abc123", 1),
        ("api-key = abc123", 1),
        ("TOKEN=xyz", 1),
        ("secret=s3cr3t", 1),
        ("passphrase=abc", 1),
        # 秘密ではなく「秘密ファイルへのパス」なので落とさない
        ("credentials=/etc/samba/smbcredentials", 0),
        ("credentials-file: /root/.cloudflared/abc.json", 0),
        ("username=nasuser", 0),
        ("noserverino,vers=3.0", 0),
        ("Description=NVR entrance", 0),
        # コマンドラインフラグの空白区切り(実機の cloudflared がこの形)
        ("--token eyJhIjoiWFla", 1),
        ("--password hunter2", 1),
        ("--api-key abc123", 1),
        ("--token=eyJhIjoiWFla", 1),
        # パスを指すフラグは落とさない(`credentials=` と同じ理由)
        ("--password-file /etc/secret.txt", 0),
        ("--credentials-file /root/.cloudflared/abc.json", 0),
        # 散文は落とさない(空白区切りをフラグ形式に限っているため)
        ("# token is required", 0),
        ("Description=token service", 0),
    ])
    def test_hit_counts(self, line, expected_hits):
        _, count = svc.redact_secrets(line)
        assert count == expected_hits, line

    def test_value_is_replaced_but_key_is_kept(self):
        out, _ = svc.redact_secrets("password=hunter2")
        assert out == f"password={svc._REDACTED}"

    def test_value_stops_at_comma_so_mount_options_survive(self):
        """値を `\\S+` で取ると、カンマ区切りのマウントオプションを丸ごと飲み込み
        **復元に必要な `noserverino` まで消える**(この Issue が保全対象として
        挙げているオプション)。実装でこれを踏んだため回帰テストを置く。"""
        out, count = svc.redact_secrets(
            "//nas/x /mnt/x cifs username=u,password=SECRET,noserverino,vers=3.0 0 0"
        )
        assert count == 1
        assert "SECRET" not in out
        assert "noserverino" in out
        assert "vers=3.0" in out
        assert "username=u" in out

    def test_cli_flag_with_space_separator_is_redacted(self):
        """実機の `/etc/systemd/system/cloudflared.service` は、トンネルトークンを
        `--token eyJ...` と**空白区切り**で直書きしている。区切りを `[:=]` だけに
        すると1件も落とせず、トークンが平文のまま NAS(CIFS の `file_mode=0664`)へ
        コピーされる。2026-09-20 に実機で踏んだため回帰テストを置く。"""
        out, count = svc.redact_secrets(
            "ExecStart=/usr/bin/cloudflared --no-autoupdate tunnel run --token eyJhIjoiWFla\n"
        )
        assert count == 1
        assert "eyJhIjoiWFla" not in out
        # 復元に必要な残りのコマンドラインは消さないこと
        assert "/usr/bin/cloudflared" in out
        assert "--no-autoupdate" in out
        assert "tunnel run" in out

    def test_flag_form_does_not_span_lines(self):
        """値の無いフラグが次の行の先頭語を巻き込まないこと。巻き込むと
        復元時に読めない台帳になる。"""
        out, count = svc.redact_secrets("ExecStart=/usr/bin/x --token\nRestart=on-failure\n")
        assert count == 0
        assert "Restart=on-failure" in out


class TestManifest:
    def test_manifest_lists_every_target_including_missing(self, tmp_path, fake_host, targets):
        outcome, _ = _run(tmp_path, fake_host)
        manifest = json.loads(Path(outcome.dest, "MANIFEST.json").read_text(encoding="utf-8"))
        paths = {f["path"] for f in manifest["files"]}
        # 不在の候補も「探したが無かった」と分かるよう残すこと
        assert "/etc/cloudflared/config.yml" in paths
        assert manifest["summary"]["missing"] >= 1
        assert manifest["summary"]["manifest_only"] == 2
        assert manifest["summary"]["copied"] == 2

    def test_paths_are_recorded_as_restore_targets_not_scan_paths(
        self, tmp_path, fake_host, targets
    ):
        """台帳と files/ の階層は**復元先のパス**であること。

        実装当初は実際に読んだパス(擬似ルート込み)をそのまま記録しており、
        「どこへ戻すのか」が台帳から分からなくなっていた。
        """
        outcome, _ = _run(tmp_path, fake_host)
        manifest = json.loads(Path(outcome.dest, "MANIFEST.json").read_text(encoding="utf-8"))
        by_path = {f["path"]: f for f in manifest["files"]}

        assert "/etc/fstab" in by_path
        assert "/etc/samba/smbcredentials" in by_path
        assert "/etc/systemd/system/nvr-entrance.service" in by_path
        assert not any(p.startswith("/tmp/") for p in by_path), by_path.keys()
        # 実際に読んだパスは source として別に残す(追跡可能性のため)
        assert by_path["/etc/fstab"]["source"].endswith("/host/etc/fstab")
        # files/ 側も復元先の階層で置かれていること
        assert Path(outcome.dest, "files", "etc", "fstab").is_file()

    def test_readme_states_that_secrets_are_absent(self, tmp_path, fake_host, targets):
        outcome, _ = _run(tmp_path, fake_host)
        readme = Path(outcome.dest, "README.txt").read_text(encoding="utf-8")
        assert "秘密ファイルの値はここには入っていません" in readme
        assert "host_config_restore.md" in readme


class TestDryRunWritesNothing:
    def test_dry_run_creates_no_files(self, tmp_path, fake_host, targets):
        outcome, dest_root = _run(tmp_path, fake_host, dry_run=True)
        assert not dest_root.exists()
        # それでも「何を拾うか」は分かること
        assert outcome.copied == 2
        assert outcome.manifest_only == 2

    def test_dry_run_summary_is_labelled(self, tmp_path, fake_host, targets):
        outcome, _ = _run(tmp_path, fake_host, dry_run=True)
        assert "【ドライラン】" in svc.format_summary_lines(outcome)[0]


class TestUnreadableFileIsNotSilentlySkipped:
    def test_permission_error_is_recorded_as_error(self, tmp_path, fake_host, targets, monkeypatch):
        """root で実行していない場合に「拾えなかった」ことが分かること。

        黙って飛ばすと、復元時に「そんなファイルがあったこと自体」を知る手段が無い。
        """
        real_stat = os.stat

        def _deny(path, *a, **k):
            if str(path).endswith("smbcredentials"):
                raise PermissionError(13, "Permission denied")
            return real_stat(path, *a, **k)

        monkeypatch.setattr(svc.os, "stat", _deny)
        records = svc.plan(root=str(fake_host))
        denied = next(r for r in records if r.path.endswith("smbcredentials"))
        assert denied.status == "error"
        assert "Permission denied" in denied.detail


class TestPrune:
    def test_generations_older_than_retention_are_removed(self, tmp_path):
        base = tmp_path / "host_config_backups"
        base.mkdir()
        now = _jst(2026, 9, 20, 4)
        old = base / "20260101_040000"
        recent = base / "20260919_040000"
        for d in (old, recent):
            d.mkdir()
            (d / "MANIFEST.json").write_text("{}", encoding="utf-8")

        removed = svc.prune(base, retention_days=30, now=now)

        assert removed == [str(old)]
        assert not old.exists()
        assert recent.exists()

    def test_directories_with_unparsable_names_are_left_alone(self, tmp_path):
        base = tmp_path / "host_config_backups"
        base.mkdir()
        human = base / "手で置いたメモ"
        human.mkdir()
        svc.prune(base, retention_days=1, now=_jst(2030, 1, 1))
        assert human.exists(), "名前が解釈できないディレクトリは消してはならない"

    def test_zero_retention_disables_pruning(self, tmp_path):
        base = tmp_path / "host_config_backups"
        base.mkdir()
        old = base / "20200101_000000"
        old.mkdir()
        assert svc.prune(base, retention_days=0, now=_jst(2030, 1, 1)) == []
        assert old.exists()

    def test_missing_base_directory_is_not_an_error(self, tmp_path):
        assert svc.prune(tmp_path / "nope") == []
