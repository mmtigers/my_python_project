# crontab

実機(Raspberry Pi)の `masahiro` ユーザーの crontab を、故障時の復旧・変更履歴管理のために
このリポジトリでも管理する(`MY_HOME_SYSTEM/deploy/systemd/` と同じ方針)。

対象ジョブは `MY_HOME_SYSTEM`・`DDD`・`docs` にまたがるため、個別プロジェクト配下ではなく
このリポジトリルートの `deploy/` 配下で管理する。

導入手順(実機側):

```bash
crontab deploy/cron/crontab
```

確認:

```bash
crontab -l
```

実機の crontab を変更した場合は、このファイルにも反映してコミットすること
(`crontab -l > deploy/cron/crontab` で同期できる)。
