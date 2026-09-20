"""ホスト側(`/etc` 等)の運用設定のバックアップ (Issue #774)。

## なぜ必要か

日次バックアップ(`services/backup_service.py`)の対象は `config.BACKUP_FILES` に
列挙されたリポジトリ配下のファイルだけで、**ホスト側の運用設定は丸ごと対象外**
だった。SDカードが飛ぶと DB とアプリは戻せても、次のものは手作業で組み直しに
なる(しかも手順書が無かった)。

- 常時録画 `nvr-*.service`(#773 でユニット本体はリポジトリ管理下に入ったが、
  実機に**実際に入っている**コピーが repo と一致しているかは分からない)
- 外部公開経路(cloudflared のトンネル設定)
- NAS マウント(`smbcredentials` と `/etc/fstab` の CIFS 設定)
- samba の設定

## 秘密情報をコピーしない方針

**秘密を含むファイルは中身をコピーせず、台帳(manifest)にメタデータだけを残す。**
記録するのはパス・所有者・パーミッション・サイズ・sha256 で、値そのものは残さない。

これは新しい方針ではなく、既にこのリポジトリが2度下している判断に揃えたもの:

- #649 — `.env` を意図的にバックアップ対象から外した(NAS 上に 664 で残った
  旧コピーの掃除も含む)
- #773 — RTSP 認証情報を `/etc/nvr/*.env`(600・root)へ切り出し、リポジトリには
  含めず「復元手順」だけを `deploy/systemd/README.md` に置いた

NAS 共有は 664 で見えるため、平文の認証情報を置けば「バックアップを取ったことで
秘密の露出面が増える」ことになる。台帳だけあれば「どのパスに・どの所有者と
パーミッションで・どんな内容(sha256)のファイルが必要か」は復元時に分かり、
**値はパスワードマネージャから入れ直す**という運用が成立する。

## 秘密でないファイルも素通しにはしない

宣言上「秘密を含まない」ファイルでも、実機の中身は環境によって違う
(例: `/etc/fstab` に CIFS の `password=` を直書きしている構成もありうる)。
コードからは実機の中身を確認できないため、**コピーする全ファイルに
`redact_secrets()` を通し、`password=`/`token=` 等の値を落とす。**
落とした件数は台帳に記録するので、素通しされたのか redact されたのかが後から分かる。

## 出力

`config.HOST_CONFIG_BACKUPS_DIR/<timestamp>/` に、

- `files/<元のパス>` — コピーした(= 秘密を含まない宣言の)ファイル
- `MANIFEST.json` — 全対象の台帳。コピーしなかった秘密ファイルもここに載る
- `README.txt` — この世代が何で、復元手順がどこにあるか

ディレクトリは 0o700 で作る(NAS のマウントオプション次第で効かないことがあるため、
効かなかった場合は台帳の `dir_mode_effective` に実際の値を残す)。
"""
from __future__ import annotations

import contextlib
import datetime
import glob
import grp
import hashlib
import json
import os
import pwd
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import config
from core.logger import setup_logging
from core.utils import get_now_jst

logger = setup_logging("host_config_backup")


@dataclass(frozen=True)
class HostConfigTarget:
    """バックアップ対象の1エントリ。

    `pattern` は glob。`secret` が True のものは**中身をコピーせず**台帳のみ。
    `repo_managed` は「リポジトリにも正がある」もので、実機のコピーが repo と
    ずれていないかを後から突き合わせるために sha256 を残す意味がある。
    """
    pattern: str
    label: str
    secret: bool = False
    repo_managed: bool = False


# 対象の宣言。**実機の `/etc` を読めない環境(CI・開発機)でも成立するよう、
# 「存在しなければスキップして台帳に missing と記録する」形にしてある。**
# 候補パスを複数挙げているものは、ディストリ・導入方法で置き場所が変わるため
# (どれが実在するかは実機で `--dry-run` を1回流して確認する)。
HOST_CONFIG_TARGETS: tuple[HostConfigTarget, ...] = (
    # --- systemd ユニット(リポジトリにも正がある。実機コピーとのドリフト検出用) ---
    HostConfigTarget("/etc/systemd/system/home_system.service", "サーバー本体", repo_managed=True),
    HostConfigTarget("/etc/systemd/system/home_dashboard.service", "ダッシュボード", repo_managed=True),
    HostConfigTarget("/etc/systemd/system/health-check.service", "起動時ヘルスチェック", repo_managed=True),
    HostConfigTarget("/etc/systemd/system/network_logger.service", "ネットワークロガー", repo_managed=True),
    HostConfigTarget("/etc/systemd/system/home_firewall.service", "ファイアウォール", repo_managed=True),
    HostConfigTarget("/etc/systemd/system/nvr-*.service", "常時録画(#773)", repo_managed=True),
    # --- NAS マウント ---
    HostConfigTarget("/etc/fstab", "マウント定義(CIFS の noserverino 等)"),
    HostConfigTarget("/etc/samba/smb.conf", "samba 設定"),
    HostConfigTarget("/etc/samba/smbcredentials", "NAS 認証情報", secret=True),
    HostConfigTarget("/etc/smbcredentials", "NAS 認証情報(別配置)", secret=True),
    HostConfigTarget("/root/.smbcredentials", "NAS 認証情報(別配置)", secret=True),
    # --- 外部公開 ---
    HostConfigTarget("/etc/cloudflared/config.yml", "cloudflared 設定"),
    HostConfigTarget("/etc/cloudflared/*.json", "cloudflared トンネル資格情報", secret=True),
    HostConfigTarget("/root/.cloudflared/*.json", "cloudflared トンネル資格情報(別配置)", secret=True),
    HostConfigTarget("/etc/systemd/system/cloudflared.service", "cloudflared ユニット"),
    # --- 録画の認証情報(#773 で切り出したもの) ---
    HostConfigTarget("/etc/nvr/*.env", "RTSP 認証情報(#773)", secret=True),
)

# redact 対象。`key=value` / `key: value` の **値だけ**を落とす。
# 「宣言上は秘密でない」ファイルの保険なので、取りこぼすより過剰に落とすほうを選ぶ。
# 次の2つは**意図的に対象外**にしている。それ自体が秘密ではなく、落とすと復元に
# 必要な情報が消えるため:
#   - `username=` — ユーザー名とパスワードが揃って秘密になる `smbcredentials` は
#     secret=True 側で扱い、そもそも中身をコピーしない
#   - `credentials=` / `credentials-file:` — 実際には**秘密ファイルへのパス**が入る
#     (`/etc/fstab` の CIFS、cloudflared の設定)。落とすと「どこを復元すれば
#     よいか」が分からなくなる
# 値は `,` / 空白 / 引用符 / `;` で止める。`\S+` にすると
# `credentials=/etc/samba/smbcredentials,noserverino,vers=3.0` のような
# カンマ区切りのマウントオプションを丸ごと飲み込み、**復元に必要な
# `noserverino` まで消してしまう**(2026-09-19 に追記された、まさにこの Issue が
# 保全対象として挙げているオプション)。
#
# 区切りは `:` / `=` だけでなく、**コマンドラインフラグの空白区切り**
# (`--token <値>`)も対象にする。実機の `/etc/systemd/system/cloudflared.service` は
# トンネルトークンを `ExecStart=... tunnel run --token eyJ...` と**空白区切り**で
# 直書きしており、`[:=]` だけを見る正規表現では1件も落とせなかった
# (2026-09-20 に実機で確認。落とした件数 0 のまま NAS へコピーされる状態だった)。
# NAS は `/etc/fstab` の CIFS オプションが `file_mode=0664` なので、
# 素通しはトンネルトークンを誰でも読める場所に置くことになる。
#
# 空白区切りを許すのは `--token` のような**フラグ形式に限る**。`token: ...` 形式まで
# 空白区切りを許すと `# token is required` のような散文の次の語まで落としてしまい、
# 復元時に読めない台帳になる。区切りは `[ \t]+` とし改行をまたがせない
# (値の無いフラグが次行の先頭語を巻き込むのを防ぐ)。
# `--password-file /etc/x` のような**パスを指すフラグは対象外**のままにする
# (`--password` の直後が `-file` で空白ではないため一致しない)。これは
# `credentials=` を意図的に残しているのと同じ理由。
_SECRET_NAMES = r"pass|passwd|password|passphrase|secret|token|api[_-]?key"
_SECRET_KEY_RE = re.compile(
    r"(?i)(?:"
    rf"\b(?:{_SECRET_NAMES})\b\s*[:=]\s*"
    r"|"
    rf"--(?:{_SECRET_NAMES})[ \t]+"
    r")(?P<value>[^\s,;'\"]+)"
)
_REDACTED = "***REDACTED-BY-host_config_backup***"


def redact_secrets(text: str) -> tuple[str, int]:
    """`password=...` 等の値を落とした本文と、落とした件数を返す。"""
    count = 0

    def _sub(m: re.Match) -> str:
        nonlocal count
        count += 1
        return m.group(0).replace(m.group("value"), _REDACTED)

    return _SECRET_KEY_RE.sub(_sub, text), count


@dataclass
class FileRecord:
    """台帳の1行。

    `path` は**復元先の絶対パス**(`/etc/fstab`)で、`source` は実際に読んだパス。
    通常は同じだが、テストで擬似ルートを使うと異なる。台帳に残すのは復元先で
    なければ意味が無い(「どこへ戻すのか」が分からなくなる)ため、両者を分けている。
    """
    path: str
    label: str
    status: str            # "copied" | "manifest_only" | "missing" | "error"
    source: str = ""       # path と異なる場合のみ記録する
    secret: bool = False
    repo_managed: bool = False
    size: int | None = None
    mode: str | None = None
    owner: str | None = None
    group: str | None = None
    sha256: str | None = None
    redactions: int = 0
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v != "" and v is not None}


@dataclass
class BackupOutcome:
    dest: str = ""
    dry_run: bool = True
    records: list[FileRecord] = field(default_factory=list)
    dir_mode_effective: str | None = None
    pruned: list[str] = field(default_factory=list)

    @property
    def copied(self) -> int:
        return sum(1 for r in self.records if r.status == "copied")

    @property
    def manifest_only(self) -> int:
        return sum(1 for r in self.records if r.status == "manifest_only")

    @property
    def missing(self) -> int:
        return sum(1 for r in self.records if r.status == "missing")

    @property
    def errors(self) -> int:
        return sum(1 for r in self.records if r.status == "error")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _owner_group(st: os.stat_result) -> tuple[str, str]:
    """uid/gid を名前に直す。名前が引けない環境では数値の文字列を返す。"""
    try:
        owner = pwd.getpwuid(st.st_uid).pw_name
    except (KeyError, OSError):
        owner = str(st.st_uid)
    try:
        group = grp.getgrgid(st.st_gid).gr_name
    except (KeyError, OSError):
        group = str(st.st_gid)
    return owner, group


def _expand(target: HostConfigTarget, root: str = "/") -> list[tuple[str, str]]:
    """glob を (復元先の絶対パス, 実際に読むパス) の組へ展開する。

    `root` はテスト用の擬似ルート。復元先は常に `/etc/...` の形にする。
    """
    pattern = target.pattern
    if root == "/":
        return [(m, m) for m in sorted(glob.glob(pattern))]
    scan_pattern = os.path.join(root.rstrip("/"), pattern.lstrip("/"))
    out = []
    for match in sorted(glob.glob(scan_pattern)):
        logical = "/" + os.path.relpath(match, root)
        out.append((logical, match))
    return out


def plan(root: str = "/") -> list[FileRecord]:
    """何をどう扱うかを決めるだけの読み取り専用フェーズ。何も書かない。"""
    records: list[FileRecord] = []
    for target in HOST_CONFIG_TARGETS:
        matches = _expand(target, root)
        if not matches:
            records.append(FileRecord(
                path=target.pattern, label=target.label, status="missing",
                secret=target.secret, repo_managed=target.repo_managed,
                detail="この環境には存在しない(候補パスの1つ)",
            ))
            continue
        for logical, match in matches:
            source = "" if logical == match else match
            try:
                st = os.stat(match)
                owner, group = _owner_group(st)
                records.append(FileRecord(
                    path=logical, label=target.label, source=source,
                    status="manifest_only" if target.secret else "copied",
                    secret=target.secret, repo_managed=target.repo_managed,
                    size=st.st_size, mode=oct(st.st_mode & 0o777),
                    owner=owner, group=group, sha256=_sha256(match),
                ))
            except OSError as e:
                records.append(FileRecord(
                    path=logical, label=target.label, source=source, status="error",
                    secret=target.secret, repo_managed=target.repo_managed,
                    detail=str(e),
                ))
    return records


def _write_manifest(dest: Path, outcome: BackupOutcome, now: datetime.datetime) -> None:
    manifest = {
        "generated_at": now.isoformat(),
        "issue": "774",
        "policy": (
            "秘密を含むファイルは中身をコピーせずメタデータのみ記録する(#649/#773 と同じ判断)。"
            "コピーするファイルも redact_secrets() を通し、password=/token= 等の値は落とす。"
        ),
        "dir_mode_effective": outcome.dir_mode_effective,
        "summary": {
            "copied": outcome.copied,
            "manifest_only": outcome.manifest_only,
            "missing": outcome.missing,
            "errors": outcome.errors,
        },
        "files": [r.as_dict() for r in outcome.records],
    }
    (dest / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


_README = """このディレクトリは MY_HOME_SYSTEM のホスト設定バックアップです (Issue #774)。

- files/ … 秘密を含まないホスト設定のコピー。元の絶対パスをそのまま階層で再現しています。
  中身は redact_secrets() を通してあるため、password=/token= 等の値は
  ***REDACTED-BY-host_config_backup*** に置き換わっていることがあります
  (置換した件数は MANIFEST.json の redactions を見てください)。
- MANIFEST.json … 全対象の台帳。**コピーしていない秘密ファイルもここに載ります**
  (パス・所有者・パーミッション・サイズ・sha256 のみ。値は記録していません)。

秘密ファイルの値はここには入っていません。復元手順は
docs/runbooks/host_config_restore.md を参照してください。
"""


def perform_backup(now: datetime.datetime | None = None, dry_run: bool = False,
                   dest_root: str | None = None, root: str = "/") -> BackupOutcome:
    """ホスト設定を収集して1世代分を書き出す。

    `dry_run=True` なら `plan()` の結果だけを返し、1バイトも書かない。
    `dest_root` / `root` はテスト用の差し替え口。
    """
    # 時刻は実時刻ではなく core/utils の JST 固定ヘルパーを使う(CLAUDE.md の規約)。
    now = now or get_now_jst()
    outcome = BackupOutcome(dry_run=dry_run, records=plan(root))
    base = Path(dest_root or config.HOST_CONFIG_BACKUPS_DIR)
    dest = base / now.strftime("%Y%m%d_%H%M%S")
    outcome.dest = str(dest)
    if dry_run:
        return outcome

    # 0o700 で作る。NAS(CIFS)のマウントオプション次第では効かないので、
    # 効かなかった実際の値を台帳に残して「秘密を置いてよい場所か」を後から判断できるようにする。
    # mkdir の mode は既存ディレクトリには効かないため、明示的に chmod も試みる。
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        os.chmod(dest, 0o700)
    try:
        outcome.dir_mode_effective = oct(os.stat(dest).st_mode & 0o777)
    except OSError:
        outcome.dir_mode_effective = None

    for record in outcome.records:
        if record.status != "copied":
            continue
        # files/ の下は**復元先のパス**で階層を再現する(実際に読んだパスではない)。
        out_path = dest / "files" / record.path.lstrip("/")
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            raw = Path(record.source or record.path).read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeError) as e:
            # テキストとして読めないもの(バイナリ等)は、値を確認できないので
            # コピーせず台帳のみに落とす。素通しでNASへ置くより安全側に倒す。
            record.status = "manifest_only"
            record.detail = f"テキストとして読めなかったためコピーしない: {e}"
            continue
        redacted, count = redact_secrets(raw)
        record.redactions = count
        try:
            out_path.write_text(redacted, encoding="utf-8")
            os.chmod(out_path, 0o600)
        except OSError as e:
            record.status = "error"
            record.detail = str(e)

    _write_manifest(dest, outcome, now)
    (dest / "README.txt").write_text(_README, encoding="utf-8")
    logger.info(
        "✅ ホスト設定をバックアップしました: %s (コピー %d / 台帳のみ %d / 不在 %d / 失敗 %d)",
        dest, outcome.copied, outcome.manifest_only, outcome.missing, outcome.errors,
    )
    if outcome.errors:
        logger.error("❌ ホスト設定バックアップで %d 件のファイルが失敗しました", outcome.errors)
    outcome.pruned = prune(base, now=now)
    return outcome


def prune(base: Path, retention_days: int | None = None,
          now: datetime.datetime | None = None) -> list[str]:
    """保持期間を超えた世代ディレクトリを削除し、削除したパスを返す。

    世代ディレクトリ名(`%Y%m%d_%H%M%S`)から日付を読む。名前が解釈できない
    ディレクトリには触らない(人が置いたものを消さないため)。
    """
    now = now or get_now_jst()
    days = retention_days if retention_days is not None else getattr(
        config, "HOST_CONFIG_BACKUP_RETENTION_DAYS", 90
    )
    if days <= 0:
        return []
    cutoff = now - datetime.timedelta(days=days)
    removed: list[str] = []
    if not base.is_dir():
        return removed
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        try:
            stamp = datetime.datetime.strptime(child.name, "%Y%m%d_%H%M%S")  # noqa: DTZ007
        except ValueError:
            continue
        # ディレクトリ名にタイムゾーンは入らないため、比較相手 `now` と同じ
        # tzinfo を付けて揃える(naive と aware を比べると TypeError になる)。
        if cutoff.tzinfo is not None:
            stamp = stamp.replace(tzinfo=cutoff.tzinfo)
        if stamp >= cutoff:
            continue
        try:
            shutil.rmtree(child)
            removed.append(str(child))
        except OSError as e:
            logger.error("❌ 古いホスト設定バックアップの削除に失敗 (%s): %s", child, e)
    if removed:
        logger.info("🧹 古いホスト設定バックアップを %d 世代削除しました", len(removed))
    return removed


def format_summary_lines(outcome: BackupOutcome) -> list[str]:
    """CLI / ログ向けの人が読む要約。"""
    head = "【ドライラン】" if outcome.dry_run else ""
    lines = [
        f"{head}ホスト設定バックアップ: {outcome.dest}",
        (
            f"  コピー {outcome.copied} 件 / 台帳のみ(秘密) {outcome.manifest_only} 件 / "
            f"不在 {outcome.missing} 件 / 失敗 {outcome.errors} 件"
        ),
    ]
    if outcome.dir_mode_effective and outcome.dir_mode_effective != "0o700":
        lines.append(
            f"  ⚠️ 出力先のパーミッションが {outcome.dir_mode_effective} です"
            + "(NAS のマウントオプションで 0o700 が効いていません)"
        )
    for record in outcome.records:
        if record.status == "missing":
            continue
        mark = {"copied": "○", "manifest_only": "秘", "error": "×"}.get(record.status, "?")
        extra = ""
        if record.redactions:
            extra = f" [値を {record.redactions} 箇所 redact]"
        if record.detail:
            extra += f" — {record.detail}"
        lines.append(f"  {mark} {record.path} ({record.label}){extra}")
    absent = [r.path for r in outcome.records if r.status == "missing"]
    if absent:
        lines.append(f"  - 不在(候補パス): {', '.join(absent)}")
    return lines
