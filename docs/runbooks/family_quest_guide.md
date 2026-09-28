# ファミクエ生活ガイド(家族向け説明ページ)の保守

Family Quest の「誰が・いつ・何をするか」「YouTube はいつ見られるか」を、パパ・ママ・ともや・
すずか向けに1枚にまとめた説明ページ。実体は claude.ai の Artifact(HTML)にあり、リポジトリには
存在しないため、`docs/runbooks/claude_routines.md` と同じ方針でここに記録する。

| 項目 | 値 |
| --- | --- |
| 現在のURL | https://claude.ai/artifact/UtjW3WYa2HA7U2awhAUx1J (非公開。ユーザー本人のみ閲覧・更新可) |
| 初版作成日 | 2026-09-26 |
| 内容の根拠 | `MY_HOME_SYSTEM/services/quest/quest_data.py`(QUESTS/USERS)、`routine_data.py`
(ROUTINE_FLOWS/DAD_ROUTINE_FLOWS/MOM_ROUTINE_FLOWS)、`config.py` の `YOUTUBE_*` 系定数 |

## 更新が必要になるタイミング

以下のいずれかを変更した場合、ガイドの内容が実態とズレるため更新する:

- `quest_data.py` の `QUESTS`(対象者・曜日・時間帯・必須/ボーナスの追加・変更・廃止)
- `routine_data.py` の `ROUTINE_FLOWS` / `DAD_ROUTINE_FLOWS` / `MOM_ROUTINE_FLOWS`
  (すごろくのステップ構成、平日/休日の分岐、チェックポイント時刻)
- `config.py` の YouTube 関連定数
  (`YOUTUBE_DAILY_LIMIT_MINUTES_WEEKDAY`/`_HOLIDAY`、`YOUTUBE_REWARD_COOLDOWN_*`、
  `YOUTUBE_EXTENSION_*`、`YOUTUBE_NAP_BLOCK_*`、`YOUTUBE_REWARD_IDS` 等)

コードレビューや仕様書ドリフト解消の作業とは独立なので、`spec-drift-sync` スキルの対象にはしていない
(あちらは `docs/specifications/` の技術文書、こちらは家族向けの平易な説明ページ)。

## 更新・アナウンスの手順

1. 上記のソースを読み直し、現状のルールを確認する(前回のガイドの記述を信用せず、コードで裏取りする)。
2. 同じ Artifact URL に対して再publishし、URL を変えない(家族が同じリンクをブックマークしていても
   最新版が表示される)。
3. ユーザーに、更新後のページを HTML ファイルとして書き出して渡す(`SendUserFile` で直接送る)。
   ユーザーがそのファイルを LINE 等で家族に共有する運用とする。
   - Gmail の `send_message` 添付ファイルは base64 をそのまま埋め込む必要があり、このページ程度の
     サイズ(約27KB → base64で36000文字超)でも手動での確実な組み立てが困難で、実際に一度
     文字破損を起こした(2026-09-26)。以後、添付メールでの送信は行わず、`SendUserFile` かArtifact
     リンクの共有を優先する。
4. ページ末尾の「最終更新」の記述(現在は「2026年9月時点の内容にもとづいて作成」)を更新日に合わせて書き換える。

## 変更履歴

- 2026-09-26: 初版作成。クエスト定義・きょうのすごろく・YouTube視聴ルールの現状を反映。
