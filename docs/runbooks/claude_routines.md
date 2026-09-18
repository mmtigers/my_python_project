# Claude Code Routine(定期実行セッション)一覧

GitHub Actions の定期ワークフロー(`spec-drift-weekly-audit.yml`、`pip-audit-weekly-audit.yml`)が
「検知して Issue に書く」までを担うのに対し、ここに列挙する Routine は Claude Code のセッションを
定期起動して「検知結果を精査し、Issue 起票や Draft PR 作成まで行う」役割を担う。
Routine の実体は claude.ai 側(Claude Code の Routines)にあり、リポジトリには存在しないため、
再作成・変更のためにスケジュールとプロンプトをここに記録する(`deploy/cron/` と同じ方針)。

共通のガードレール:

- master への直接 push、既存ブランチへの force push、PR のマージ・承認は行わない。
  成果物は Issue または Draft PR のみで、取り込みは人が判断する。
- 1回の実行で起票する Issue は最大 10 件。既存の open Issue と重複する内容は起票しない
  (起票前に `search_issues` で確認する)。
- Dependabot のコミットはレビュー対象外。

## 1. 週次リポジトリ品質監査

| 項目 | 値 |
| --- | --- |
| スケジュール | 毎週月曜 10:00 JST (`0 1 * * 1` UTC)。同日 09:00 JST の週次ワークフロー群の後 |
| セッション | 毎回新規セッション(fresh) |
| 成果物 | GitHub Issue(タイトル接頭辞 `[週次品質監査]`) |

背景: 2026-09-04〜06 に手動セッションで「全体コードレビュー → Issue 大量起票 → 一括対応」を
回した(PR #354/#433/#480/#506/#526/#545)。この「レビューして Issue にする」工程を毎週の
差分に絞って定期化する。修正の適用は従来どおり人が Issue を読んで判断する。

プロンプト:

```
リポジトリ mmtigers/my_python_project の週次品質監査を行ってください。

1. まずルートの CLAUDE.md を読み、このリポジトリの規約(レイヤリング、get_db_cursor、
   migrations、config.py/.env.example、仕様書ドリフト規約、削除済み機能)を把握する。
2. `git log --since=8.days --no-merges origin/master` で直近1週間に master へ入った変更を列挙し、
   Dependabot のコミットを除いた差分(`git diff <1週間前の最初のコミット>^..origin/master`)を
   レビューする。観点: 正しさ/バグ、境界条件、エラーハンドリング、規約違反、テスト漏れ、
   仕様書(docs/specifications/)との不整合。
3. 見つけた指摘は、必ず該当コードを読み直し、可能ならテストや再現スクリプトを
   MY_HOME_SYSTEM/ で実行して裏取りする(環境変数 SQLITE_DB_PATH=":memory:"、
   NAS_MOUNT_POINT="./tmp_nas"、NOTIFICATION_TARGET="none" を設定して pytest を実行できる)。
   裏取りできない指摘・好みの範囲の nit は起票しない。
4. 裏取りできた指摘ごとに、既存の open Issue に同じ内容が無いか search_issues で確認したうえで、
   タイトル `[週次品質監査] <要約>` の Issue を作成する。本文には、対象ファイルと行、問題の説明、
   再現手順または根拠、修正案、重要度(High/Medium/Low)を書く。1回の実行で最大 10 件。
5. コードの変更・push・PR 作成は行わない(Issue 起票のみ)。
6. 最後に、レビューした範囲(コミット数・ファイル数)と起票した Issue の一覧、起票しなかった
   指摘があればその理由を要約して報告する。指摘が無ければ Issue を作らず、その旨だけ報告する。
```

## 2. 週次仕様書ドリフト解消

| 項目 | 値 |
| --- | --- |
| スケジュール | 毎週月曜 11:00 JST (`0 2 * * 1` UTC)。`spec-drift-weekly-audit.yml`(09:00 JST)が Issue #20 を更新した後 |
| セッション | 毎回新規セッション(fresh) |
| 成果物 | Draft PR(ブランチ `claude/spec-drift-weekly-<日付>`)。検知事項が無ければ何もしない |

背景: 週次監査は Issue #20 を更新するだけで、解消は人手だった(2026-09-06 に7件を手動で精査・解消)。
リポジトリの `spec-drift-sync` スキルが「ソース変更に対応する仕様書の追従」を定義しているので、
監査 Issue に検知が出た週は同スキルで仕様書を追従させ、Draft PR として提示する。

プロンプト:

```
リポジトリ mmtigers/my_python_project の仕様書ドリフトを解消してください。

1. ルートの CLAUDE.md の「仕様書ドリフト規約」を読む。
2. GitHub Issue #20(「📋 仕様書ドリフト定期監査」)の本文を読み、最新の検知結果を確認する。
   「検知事項なし」なら何もせず、その旨だけ報告して終了する。
3. 検知事項がある場合は、`python3 .github/scripts/check_spec_drift.py full --out drift-report.md`
   をローカルでも実行して現在の状態を再確認する。
4. ドリフトした各ファイルについて、spec-drift-sync スキルの手順に従い、ソースを読み直して
   docs/specifications/ 配下の対応する仕様書を追従させる(仕様書のフォーマット・
   「解析基準コミット」行・根拠の行番号の規約を守る)。孤立ドキュメント(ソースが削除済み)は
   対応する README の廃止済み一覧へ移し、仕様書が無い新規ソースは新規作成する。
   ソースコード(MY_HOME_SYSTEM/、DDD/、family-quest/src/)は一切変更しない。
5. 再度 check_spec_drift.py full を実行し、検知事項が減っていることを確認する。
6. ブランチ `claude/spec-drift-weekly-<YYYYMMDD>` を master から作成し、コミット・push して
   Draft PR を作成する。PR 本文には Issue #20 への参照、追従したファイルの一覧、
   判断に迷った点を書く。master へは直接 push しない。
7. 最後に、PR の URL と、解消できなかった検知事項(あれば理由つき)を報告する。
```

## 変更・停止の方法

Claude Code のセッションから `list_triggers` / `update_trigger` / `delete_trigger`(claude-code-remote MCP)
で確認・変更・削除できる。プロンプトを変えたら本ファイルも更新すること。
