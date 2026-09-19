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

## Bluetoothスピーカーのキープアライブ — 登録していない(2026-09-19 判断)

`tools/keep_alive_anker.sh`(Issue #585)は #585/#664 で5分毎(`*/5`)の実行として
登録していたが、**2026-09-19 の実機棚卸しで登録を外した**。

理由: `config.ENABLE_BLUETOOTH` は既定 `False`、実機 `.env` の
`SPEAKER_BLUETOOTH_MAC` も未設定で、5分毎の実行が一度も鳴動せず no-op で
正常終了し続けていた(=BTスピーカー運用そのものが休止中だった)。
ユーザー判断で「休止で確定」とし、空振りの定期実行をやめた。

スクリプト(`tools/keep_alive_anker.sh` / `tools/connect_speaker.sh`)は削除していない。
`config.py` の `ENABLE_BLUETOOTH` のコメントが再有効化手順として
「`tools/connect_speaker.sh` の定期実行の整備」を挙げており、休止解除に必要な資産だからである。
運用を再開する手順とエントリの雛形は `deploy/cron/crontab` の該当コメントにある。
`.github/scripts/test_crontab_keep_alive.py` が「`ENABLE_BLUETOOTH` の値と cron 登録の有無が
一致すること」を検査するため、フラグだけ `True` にして登録を忘れる/その逆はCIで落ちる。

再開する際の間隔(`*/5`)は、Bluetoothスピーカーの一般的なオートパワーオフ時間
(10〜20分程度)より十分短い間隔という一般論に基づく暫定値で、実機の対象デバイスの
実際のオートオフ時間は未確認のままである。実態が分かった場合は合わせて調整すること。

### `keep_alive_speaker.sh` を廃止して `keep_alive_anker.sh` へ一本化した理由 (Issue #664)

#585 の時点では「Bluetoothスピーカーのキープアライブ」という同じ目的を別方式で
達成する2スクリプトが並存し(PipeWire接続監視+15Hz正弦波 / mpg123での無音MP3再生)、
どちらが後継でどちらがレガシーかも明記されていなかったため、実行契機が皆無だった
状態の解消を優先して**両方を**登録していた。#664 で2つの実装を比較し、
`keep_alive_anker.sh` を残して `keep_alive_speaker.sh` を削除した。判断根拠:

1. **機能が包含関係にある**。`keep_alive_anker.sh` は
   「`pactl list sinks short` で接続確認 → 未接続なら `tools/connect_speaker.sh`
   で再接続 → キープアライブ信号を送出」を行う。`keep_alive_speaker.sh` は
   無音MP3を再生するだけで、**切断されている状態からは復帰できない**
   (`mpg123` が失敗してエラーログが1行増えるだけ)。キープアライブの目的である
   「スピーカーを使える状態に保つ」を単独で満たせるのは前者のみ。
2. **NASマウントに依存しない**。`keep_alive_speaker.sh` の音源は
   `/mnt/nas/home_system/assets/sounds/silent.mp3` で、NAS未マウント時は
   キープアライブが丸ごと機能しなくなる(この点は仕様書の「保守上の注意点」にも
   記載済みだった)。`keep_alive_anker.sh` は `sox` で信号をその場で生成するため
   ファイル依存が無い。
3. **設定の外部化が済んでいる**。`keep_alive_anker.sh` は Issue #663 で
   スピーカーのMACアドレスを `.env` の `SPEAKER_BLUETOOTH_MAC` から読むようになり、
   未設定時は no-op で正常終了する。`keep_alive_speaker.sh` は
   ログ・音源とも絶対パスがハードコードされたままだった。
4. **ログが運用に耐える**。`keep_alive_anker.sh` は #249 で `log()` 呼び出しごとに
   時刻を評価するよう修正済みで、正常時は何も出力しない。
   `keep_alive_speaker.sh` は成功時も毎回2行書くため、5分毎の実行では
   `bluetooth_monitor.log` に1日あたり約576行のノイズが積み上がっていた。

残した `keep_alive_anker.sh` の側の注意点として、**`sox` コマンドへの依存**がある
(未インストール時は `[ERROR] 'sox' command not found` をログに残して何もしない)。
実機で `sox` が入っていることを前提にしているので、OS再構築時は
`sudo apt install sox` を忘れないこと。

なお `config.py` の `ENABLE_BLUETOOTH` は既定 `False` で、BTスピーカー運用自体が
現在は休止中である。再開する際は本エントリの間隔と `sox` の有無をあわせて確認すること。
