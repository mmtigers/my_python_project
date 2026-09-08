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

反映漏れは、毎時cronの `MY_HOME_SYSTEM/monitors/health_watch.py`(チェック7: 実機構成ドリフト検知)が
`crontab -l` と本ファイルをコメント・空行を除いて比較し、差分があれば Discord の error チャンネルへ
通知する(自動で書き戻しはしない)。

`run_task.sh` を経由しないエントリ(`newface_monitor.py`等)がログを
リポジトリ管理外のディレクトリ(`.gitignore`対象)へリダイレクトする場合は、
そのディレクトリが実機に存在する保証が無い(`run_task.sh`経由のエントリは
`start_all.sh`起動時の`mkdir -p logs`に暗黙で依存できるが、これらは依存できない)。
存在しないディレクトリへのリダイレクトはシェル側で無言のまま失敗し、
Pythonプロセス自体が一切起動しないため、該当エントリには`mkdir -p`を
リダイレクトより前に明示的に含めること(Issue #587)。
