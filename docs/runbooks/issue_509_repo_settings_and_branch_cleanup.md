# Issue #509 対応手順書: branch protection設定・ブランチ棚卸し

このファイルはIssue #509の調査結果を、オーナー(GitHub UI操作・push権限)が実施するための
手順としてまとめたものである。Claude Codeのセッションからは以下いずれも実行できないこと
を確認済み(設定変更APIが無い/ブランチ削除APIが無い/`git push --delete`はpush権限不足で403)。

## 1. branch protectionの設定 (F-1)

GitHub UI: `Settings` → `Branches` → `Add branch protection rule`

対象: `master`

| 設定項目 | 値 |
| --- | --- |
| Branch name pattern | `master` |
| Require status checks to pass before merging | 有効 |
| └ 対象チェック | `Lint`, `Test + Coverage`, `Security Scan`, `Frontend Build (family-quest)` の4つ |
| Require branches to be up to date before merging | 無効 |
| Require pull request reviews before merging | 無効(単独開発者のため) |
| Include administrators | 無効(緊急時に自分で回避できる余地を残す) |
| Allow force pushes | 無効 |
| Allow deletions | 無効 |

**注意**: `Claude PR review` と `Check spec/source drift` は required に含めないこと。
前者はDependabot PRで`skipped`になる場合があり、後者は設計上常に非ブロッキング
(`continue-on-error: true`)である。どちらもrequiredにすると、正当な状態でも
マージが永久にブロックされる。

## 2. Automatically delete head branches の有効化 (F-2前段)

GitHub UI: `Settings` → `General` → `Pull Requests` → `Automatically delete head branches` にチェック

これにより、今後PRがマージされるたびにheadブランチが自動削除され、
今回のような滞留の再発を防ぐ。

## 3. 既存の滞留ブランチの棚卸し (F-2本体)

### 調査方法

`git ls-remote --heads origin`(186本)と、GitHub上の全PR履歴(open 18件 + closed 495件、
`#1`〜`#513`全件)のheadブランチ名を突き合わせ、各ブランチを3分類した。

### 分類結果

| 分類 | 本数 | 扱い |
| --- | --- | --- |
| `master` | 1 | 対象外 |
| open中のDependabot PRのheadブランチ | 18 | 対象外(PRがopenである限り触らない) |
| closed PRに対応するブランチ | 163 | **削除して問題なし**(下記参照) |
| どのPRにも対応しない孤立ブランチ | 4 | 個別に確認 |

### 163本が「削除して問題なし」と判断できる理由

これらは全て、GitHub上の何らかのclosed PR(マージ済みまたは却下済み)のheadブランチである。
PRがcloseされている場合、そのPRページ自体は**ブランチを削除してもGitHub上に恒久的に残り**、
diff・コミット履歴・コメントは全て閲覧可能なままである。したがって、ブランチという「作業中の
参照」を削除しても、そのPRで行われた作業記録が失われることはない。

削除対象ブランチの一覧・実行コマンドは本ファイルと同じセッションで生成した
`/tmp/safe_delete_candidates.txt`(ブランチ名一覧)・`/tmp/delete_commands.txt`
(`git push origin --delete <branch>`形式のコマンド一覧、163行)を参照。
これらは一時ファイルのためセッション終了後は残らない — 再生成する場合は、
リポジトリルートで以下を実行する:

```bash
git fetch origin --prune -q
git ls-remote --heads origin | awk '{sub("refs/heads/", "", $2); print $2}' > /tmp/current_branches.txt
# GitHub側でopen PRのheadブランチ一覧を取得し、上記から除外
# (本調査時点のopenは全てdependabot/*の18本)
```

削除は以下のいずれかで実行できる:
- GitHub UIの「Branches」タブから個別に削除(ボタン一つ、本数が多いと手間)
- `gh` CLIがある環境で `gh api` を使ったスクリプト一括削除
- ローカルで `git push origin --delete <branch1> <branch2> ...` を分割実行(1回のコマンドで
  渡せる本数に注意。GitHub側のリクエストサイズ制限に応じて10〜20本ずつ程度に分けるのが無難)

**推奨**: 一度に163本消す前に、まず10本程度で試し、GitHub Actions等に副作用が
出ないことを確認してから残りを実行する。

### 個別確認が必要な4本

| ブランチ名 | 最終コミット日 | 状況 |
| --- | --- | --- |
| `claude/issue-507-508-fv7ne8` | 2026-09-06 | 前回セッションでClaudeが手順誤りにより作成した作業ブランチ。中身はPR #511/#512で既にマージ済みで重複。**削除して問題なし**。 |
| `test/push-check` | (push権限確認用) | push権限確認のための使い捨てブランチ。masterとの差分なし。**削除して問題なし**。 |
| `feature` | 2025-12-30 | リポジトリ最初期の、現行のPRベース運用が確立する前のブランチ。現行masterとの差分が数万行規模(履歴分岐によるものだが実質的な内容は個別確認が必要)。**削除前に中身の確認を推奨**。 |
| `claude/family-quest-organization-dly8ma` | 2026-08-20 | 同上、PR運用外で作成された古いブランチ。**削除前に中身の確認を推奨**。 |

## 4. 完了確認

上記1・2はGitHub UI上ですぐ確認できる(Settingsページの表示、および次回PRマージ時に
headブランチが自動削除されるか)。3(棚卸し)が完了したら、Issue #509・#481のクローズを
検討する。
