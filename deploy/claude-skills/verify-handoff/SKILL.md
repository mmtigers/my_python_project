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

### 自動検出の対象外の言語(Go / Rust など): verify.json を手書きする
自動検出は Python(ruff / mypy / pytest)・Node(package.json の lint / typecheck / test / build)・Makefile のみ。`go.mod` や `Cargo.toml` だけのリポジトリでは検証が 0 件になる(`vh.py run` は終了コード1)ので、`<repo>/.claude/verify.json` を作る。軽い順に並べる。

```json
{"checks": [
  {"name": "go-vet",  "cmd": "go vet ./..."},
  {"name": "go-test", "cmd": "go test ./...", "timeout": 900}
]}
```
```json
{"checks": [
  {"name": "cargo-clippy", "cmd": "cargo clippy --all-targets -- -D warnings"},
  {"name": "cargo-test",   "cmd": "cargo test", "timeout": 1200}
]}
```
サブディレクトリなら `cwd`、実行しなくてよい補助チェックなら `"optional": true` を付ける。ツール未導入は `env` と記録される。

## 手順
1. 事前確認: `git status -sb`。依存が未導入なら導入手順をCLAUDE.mdから確認(導入は仮想環境内で。グローバルを汚さない)。
2. `vh.py detect` で実行内容を確認 → `vh.py run`(`--only NAME...` で部分実行)。軽い順(lint→型→テスト→ビルド)に実行し、失敗しても残りを続けて結果を揃える。
3. 失敗は出力末尾を読んで原因を特定。自分の変更が原因なら直して該当チェックだけ再実行 → 最後に全体を1回。環境起因(依存・権限・ネットワーク)で実行できないものは「未検証」と明記する。
4. `vh.py handoff --unresolved "..." --next "..."` で `.claude/handoff/<branch>.md` を生成。検証後にHEAD・未コミット差分・verify.json のいずれかが変わると警告が出るので再実行する。`--only` の部分実行は、状態が同じ場合だけ既存結果へマージされ、それ以外は「部分実行・未実行チェック」と明記される。
5. 出力先 `.claude/handoff/` は初回書き込み時に `.git/info/exclude` へ自動登録される(作業ツリーを汚さない)。クラウド等で環境が消える場合は、内容をPR本文/コミット/ユーザーへの報告に転記する。

## 報告
各チェックの pass/fail/時間、変更ファイル、失敗の根本原因、未検証項目、次の作業。実行していない検証を成功と書かない。

## 注意
- `cmd` はシェルでそのまま実行される。**信頼できないリポジトリでは `detect` の内容を確認してから** `run` すること(推定コマンドも同様)。
- `env` 判定は出力の文字列判定なので、自分の import ミスも `env` になりうる。出力末尾を必ず読む。
- `CLAUDE_SKILLS_DIR` で別の場所へ入れた場合、本書のパスは読み替える。
