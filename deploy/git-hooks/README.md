# git hooks

実機(Raspberry Pi)のリポジトリで使う git フックを、`deploy/cron/`・`MY_HOME_SYSTEM/deploy/systemd/`
と同じ方針でこのリポジトリで管理する。

| フック | 役割 |
| --- | --- |
| `post-merge` | `git pull` 後に `family-quest/deploy.sh --if-stale` を実行し、配信用ビルド `dist/` を最新化する(family-quest に変更が無ければ何もしない)。 |

## 登録(実機側)

`MY_HOME_SYSTEM/start_all.sh` の Phase 1.6 が、サーバー起動のたびに次を冪等に実行するため、
通常は手動登録は不要(clone し直しても次回の `start_all.sh` 実行で自動的に登録される)。

```bash
git config core.hooksPath "$(git rev-parse --show-toplevel)/deploy/git-hooks"
```

確認:

```bash
git config --get core.hooksPath
```

## 注意

- `core.hooksPath` を設定すると `.git/hooks/` 配下のフック(以前ローカル設置していた
  `post-merge` を含む)は一切実行されなくなる。追加のフックが必要なら、このディレクトリに
  実行権限付き(`chmod +x`)で置いてコミットすること。
- フックは CI の shellcheck(`test.yml` の lint ジョブ)の対象に含まれている。フックを追加したら
  `test.yml` の shellcheck 対象リストにもファイル名で追加すること(README を巻き込まないよう glob は使わない)。
