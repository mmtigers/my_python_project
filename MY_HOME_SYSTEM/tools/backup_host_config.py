#!/usr/bin/env python3
# MY_HOME_SYSTEM/tools/backup_host_config.py
"""ホスト側(`/etc` 等)の運用設定をバックアップする CLI (Issue #774)。

実体は `services/host_config_backup_service.py`。SDカードが飛んだときに
常時録画(nvr)・外部公開(cloudflared)・NASマウント(smbcredentials / fstab)を
手作業で組み直さずに済むよう、これらを NAS の
`config.HOST_CONFIG_BACKUPS_DIR` へ1世代ずつ書き出す。

**秘密を含むファイルは中身をコピーせず、台帳(MANIFEST.json)にパス・所有者・
パーミッション・sha256 だけを記録する**(#649 で `.env` を対象外にした判断、
#773 で RTSP 認証情報をリポジトリに含めなかった判断と同じ)。

使い方(実機の MY_HOME_SYSTEM/ で):

    sudo .venv/bin/python tools/backup_host_config.py --dry-run  # 何を拾うかの確認(書かない)
    sudo .venv/bin/python tools/backup_host_config.py            # 1世代を書き出す

`/etc/nvr/*.env`(600・root)や `smbcredentials` の**メタデータ**を読むために
root 権限が必要。root でなくても動くが、読めなかったものは台帳に error として
残る(パーミッションで弾かれたことが分かるようにするため、黙って飛ばさない)。

候補パスを複数宣言しているもの(cloudflared / smbcredentials の置き場所など)は、
**実機で一度 `--dry-run` を流して「不在」として出た候補を確認し**、実態に合わせて
`HOST_CONFIG_TARGETS` を削ること。コードからは実機の配置を確認できない。

復元手順: `docs/runbooks/host_config_restore.md`
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from services import host_config_backup_service as svc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ホスト側(/etc 等)の運用設定をバックアップする (Issue #774)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="何を拾うか・どれが秘密扱いかを表示するだけで、1バイトも書かない",
    )
    parser.add_argument(
        "--dest", default=None,
        help=f"出力先のルート(既定: {config.HOST_CONFIG_BACKUPS_DIR})",
    )
    args = parser.parse_args(argv)

    outcome = svc.perform_backup(dry_run=args.dry_run, dest_root=args.dest)
    print("\n".join(svc.format_summary_lines(outcome)))
    if outcome.pruned:
        print(f"  🧹 保持期間超過の世代を {len(outcome.pruned)} 件削除しました")

    # 失敗の判定は errors を先に見る。読めなかったファイルは copied にも
    # manifest_only にも入らないため、順序を逆にすると root で実行していない
    # ケースが「候補パスが実態と合っていません」と誤って案内される。
    if outcome.errors:
        print(
            f"\n⚠️ {outcome.errors} 件のファイルで失敗しました"
            "(root で実行していない場合はパーミッションを確認してください)。",
            file=sys.stderr,
        )
        return 1
    if not args.dry_run and outcome.copied == 0 and outcome.manifest_only == 0:
        print(
            "\n⚠️ 対象が1件も見つかりませんでした。実機以外で実行したか、"
            "HOST_CONFIG_TARGETS の候補パスが実態と合っていません。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
