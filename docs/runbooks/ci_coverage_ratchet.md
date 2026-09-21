# カバレッジのラチェットが正しい比較元を見ているかの確認

**対象**: `.github/workflows/test.yml` の `test` / `frontend` ジョブと、
`.github/scripts/check_coverage_ratchet.py` / `.github/scripts/find_coverage_baseline.sh`

## この runbook の目的

カバレッジのラチェット（master の実測から 0.5pt を超えて下がったら PR を失敗させる仕組み）は、
**比較元として選んだ master の run が正しいときにしか意味を持たない**。比較元が古いと、
チェック自体は緑のまま、実際には大きな低下を通してしまう。2026-09-21 に実際にそれが起きた。

## 2026-09-21 の事故

PR #839 の最初の CI 実行（run 35566863773）で、backend / frontend の両ラチェットが比較元に
選んだのは **2026-09-10 の run 34475282120**（run_number 946）だった。同日 05:25 に
`conclusion=success` で完了していた master の run 35564515567（run_number 1266）ではない。

| ラチェット | 比較元として読んだ値 | 同時点の master の実測 | 差 |
| --- | --- | --- | --- |
| backend | 74.76% | 86.00% | 11.24pt |
| frontend | 47.22% | 78.64% | 31.42pt |

つまり「0.5pt を超える低下で失敗」というゲートが、実際には 11〜31pt の低下まで通す状態だった。
閾値の手動引き上げを不要にする目的で導入した仕組み（Issue #494 / #536）が、その目的を
ほとんど果たしていなかったことになる。

## 原因について分かっていること / 分かっていないこと

当初は「`?branch=master&status=success&per_page=1` が直近の run を返さない」ことが原因だと
考えたが、**この前提は後の調査で誤りだと分かった**。事故の翌日に GitHub API を直接叩いた結果:

```console
$ API=https://api.github.com/repos/mmtigers/my_python_project/actions/workflows/test.yml/runs
$ curl -sS "$API?branch=master&status=success&per_page=1" | jq -r '.workflow_runs[0].id'
35568917512            # 正しく最新 run が返る

$ curl -sS "$API?branch=master&status=success&per_page=100" \
    | jq -r '[.workflow_runs[].created_at] as $c
             | if $c == ($c|sort|reverse) then "降順で正しい" else "降順になっていない" end'
降順で正しい           # 並びも崩れていない

$ curl -sS "$API?branch=master&status=success&per_page=100" \
    | jq -r '[.workflow_runs[]] | to_entries
             | map(select(.value.id == 34475282120)) | .[] | "index=\(.key)"'
index=95               # 問題の run は 96 番目に位置しており、先頭ではない
```

`per_page=1` を5回続けて引いても結果は揺れなかった。したがって**クエリの組み立ての不具合ではなく、
事故当時に API が古い結果を返した（一過性のもの）**と考えるのが妥当である。
**根本原因（なぜ API がその時だけ古い結果を返したか）は特定できていない。**

## そのため置いている防御

原因を特定できていない以上、引き方を変えるだけでは再発を防げない。
`find_coverage_baseline.sh` に、原因に依存しない防御を2つ置いてある。

1. 並びをサーバーに任せず、jq 側で `created_at` の降順に並べ替えてから先頭を採る
2. 選ばれた比較元が `STALE_AFTER_DAYS`（既定 3 日）より古ければ `::warning::` を出す

いずれも `.github/scripts/test_find_coverage_baseline.py` が回帰テストで固定している
（並べ替えを外す・警告を外すと落ちることを確認済み）。

## 再発を疑ったときの確認手順

1. PR の CI ログで `Find latest successful master run` ステップを開き、
   `baseline run: <id> <created_at>` と `baseline age: N 日` を見る。
   `::warning::` が出ていれば、その時点で比較元が古い。
2. その run が本当に master の直近成功 run かを確認する。

   ```bash
   gh api "repos/mmtigers/my_python_project/actions/workflows/test.yml/runs?branch=master&status=completed&per_page=20" \
     --jq '[.workflow_runs[] | select(.conclusion=="success")]
           | sort_by(.created_at) | reverse
           | .[0:3][] | "\(.run_number)\t\(.created_at)\t\(.id)"'
   ```

3. ログの `[coverage-ratchet] <label>: OK (master X% → PR Y%)` の X が、master 側の
   直近 run のログに出ている `Total coverage:` と一致しているかを突き合わせる。
   ここがずれていたら、その PR のラチェットは意味を成していない。
4. ずれていた場合、その PR のカバレッジ低下はラチェットでは検知できていない。
   固定の床（`--cov-fail-under` / `vitest.config.ts` の `coverage.thresholds`）だけが
   効いていたものとして、必要なら手作業で master と突き合わせる。

## 床（固定閾値）との関係

ラチェットは `pull_request` イベントでしか走らず、比較元の成果物（保持14日）が取れない
ときはスキップされる。上記のように比較元が誤っている可能性もある。
**したがって床はラチェットの単なる冗長化ではなく、最後の砦である。**
実測が伸びたら Issue #494 / #536 と同じ運用で床も段階的に引き上げること
（現在: MY_HOME_SYSTEM 84 / DDD 74 / family-quest は `vitest.config.ts` の
`coverage.thresholds`）。
