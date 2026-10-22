---
name: verify-handoff
description: プロジェクトに存在するLint・型チェック・テスト・ビルドを軽い順に実行して結果を記録し、変更ファイル・失敗・未解決事項・次の作業をまとめた引き継ぎメモを作る。「検証して」「テスト一式」「引き継ぎ」「作業まとめ」「セッションを終える前」に使う。
---

# verify-handoff

補助: `python3 ~/.claude/skills/verify-handoff/scripts/vh.py <detect|run|handoff>`(標準ライブラリのみ)。

## 検証コマンドの決まり方
1. `<repo>/.claude/verify.json`(プロジェクト固有・優先)。形式は `vh.py --help` 参照: `checks[]` に `name, cwd, cmd, env, timeout, optional`。
2. 無ければ package.json scripts / ruff.toml / pytest.ini / Makefile から推定(`vh.py detect` で確認)。推定は保守的なので、重要なリポジトリでは verify.json を作ること。
3. CLAUDE.md にCI相当の手順があれば、それを verify.json に写す(CIのブロッキング項目を優先)。

## 手順
1. 事前確認: `git status -sb`。依存が未導入なら導入手順をCLAUDE.mdから確認(導入は仮想環境内で。グローバルを汚さない)。
2. `vh.py detect` で実行内容を確認 → `vh.py run`(`--only NAME...` で部分実行)。軽い順(lint→型→テスト→ビルド)に実行し、失敗しても残りを続けて結果を揃える。
3. 失敗は出力末尾を読んで原因を特定。自分の変更が原因なら直して該当チェックだけ再実行 → 最後に全体を1回。環境起因(依存・権限・ネットワーク)で実行できないものは「未検証」と明記する。
4. `vh.py handoff --unresolved "..." --next "..."` で `.claude/handoff/<branch>.md` を生成。検証後にHEADが変わっていれば警告が出るので再実行する。
5. 引き継ぎメモは既定でgit管理外(`.claude/handoff/`)。クラウド等で環境が消える場合は、内容をPR本文/コミット/ユーザーへの報告に転記する。

## 報告
各チェックの pass/fail/時間、変更ファイル、失敗の根本原因、未検証項目、次の作業。実行していない検証を成功と書かない。
