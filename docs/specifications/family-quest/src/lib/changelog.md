## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | changelog.ts |
| 言語 | TypeScript |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

* [../components/ui/ChangelogModal.md](../components/ui/ChangelogModal.md) - 本データの表示側

## 2. ファイルの概要

* アプリ内「アップデートのれきし」に表示する更新履歴を、型付き定数 `CHANGELOG`（新しい順、先頭が最新）として公開する。**(変更)** データの正は同じディレクトリの `changelog.json`(`import changelogData from './changelog.json'`で読み込み、`ChangelogEntry[]`として公開する)。更新のたびに `changelog.json` へ手で追記する。JSONにしたのは、お家ダッシュボード(MY_HOME_SYSTEM)の「アップデート」ページが同じファイルを直接読んで全アプリの更新履歴を一覧表示するため(TypeScriptを解析せずに済み、二重管理にもならない)。
* `version` は履歴専用の版数で、`package.json` の version とは連動しない。
* 各エントリは `version`（文字列）・`date`（`YYYY-MM-DD`）・`changes`（文字列配列）を持つ（`ChangelogEntry`）。
* 新しい順・版数の一意性・日付形式は `ChangelogModal.test.tsx` が検証する。

### 追加・変更（2026-10-10: 記録への券使用表示・テレビおやすみ/アップデートの画面上部表示）

* `CHANGELOG`（`changelog.json`）の先頭に v1.3.0（2026-10-10）を追記した。先頭エントリの版数は `changelogSeen.ts` の既読判定（`LATEST_CHANGELOG_ENTRY`）の基準にもなるため、新しい版を追記するとバッジ・帯が再び出る。
* 根拠: `changelog.json` の先頭付近の `"version": "1.3.0"` のエントリ(データはJSONに移したため、`changelog.ts`内の行番号引用は持たない)

### 追加・変更（2026-10-10: おしらせを「記録」ボタンの横へ移動）

* `CHANGELOG`（`changelog.json`）の先頭に v1.3.1（2026-10-10）を追記した。
* 根拠: `changelog.json` の先頭の `"version": "1.3.1"` のエントリ
