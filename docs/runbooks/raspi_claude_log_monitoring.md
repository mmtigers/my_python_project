# ラズパイ自動ログ監視・調査ランブック（設計）

Claude Code がラズパイ（`home_system.service` 稼働機）にSSH接続し、ログを定期監視 → 異常検知時に調査・原因特定・改修案提示までを自動化するための構成メモ。**現時点では設計段階であり、未実装**（実装には後述の準備が必要）。

## 目的

- ラズパイ側の障害（`home_system.service` のクラッシュ、ディスク/メモリ逼迫、NASマウント断など）を、人間が能動的にダッシュボードを見に行かなくても検知したい。
- 検知した異常について、原因調査・改修案の提示までを自動化し、対応の初動を早める。
- ただし、自動適用（コード修正の自動デプロイ・`systemctl restart` の自動実行）は行わない。ラズパイは家庭用の本番システムであり、誤診断による停止のリスクを避けるため、**「調査・提案」までを自動化し、適用は人間判断**とする。

## 全体構成

```
[Cloudflare Zero Trust]
  Access Application (SSH) + Service Token
        │  (cloudflared access ssh)
        ▼
[Claude Code Remote 環境] ──(定期Routine、目安1時間毎)──▶ ラズパイへSSH
        │
        ├─ 1. 一次チェック（同一セッション内、SSH経由・低コスト）
        │     - journalctl -u home_system.service --since <前回マーカー>
        │     - logs/*.log の ERROR/CRITICAL grep
        │     - ディスク/メモリ使用率の閾値チェック（df, free）
        │     - マーカーファイル（前回チェック時刻）を更新
        │
        ├─ 2. 異常なし → 何もせず終了（通知なし。トークン消費を抑える）
        │
        └─ 3. 異常あり → 詳細調査
              - 本リポジトリのソース（services/*, routers/* 等）と突き合わせて原因を特定
              - 改修案（diff）を作成
              - GitHub Issue（または Draft PR）として起票
              - notification_service.py と同じDiscord/LINE Webhookへ要約を通知
              - 自動適用・自動再起動はしない
```

なぜこの構成か（採用しなかった案との比較）:

| 案 | 内容 | 不採用/補助的な理由 |
| --- | --- | --- |
| ラズパイ上で完結 | `cron` が `claude -p` をローカル実行し、`journalctl` を直接読む | ラズパイ本体が落ちた場合に自己診断できない |
| クラウド側から常時定期SSH | 毎回フル調査を実施 | 平常時（異常なし）でも毎回コストがかかる |
| **ハイブリッド（採用）** | 定期SSHの中で「安価な一次チェック→異常時のみ詳細調査」の2段構成 | 両者の欠点を回避。異常検知はクラウド側で完結しつつ、コストは異常時のみ |

SSH到達性は、`start_all.sh` の `switchbot_webhook_fix.py`（Cloudflare Tunnel経由のWebhook修正）で既に使われている Cloudflare Tunnel を流用し、`cloudflared access ssh`（Cloudflare Access の短命トークン経由）を拡張する。22番ポートをWANに直接公開せず、新規VPN（Tailscale等）の追加導入も不要。

## 必要な準備（ユーザー側の作業）

実装再開時にこの3点が完了している必要がある。

1. **Cloudflare Zero Trust**: SSH用のSelf-hosted Access Applicationを作成し、Service Token（Client ID / Secret）を発行。既存のTunnel設定に、ラズパイの22番ポート向けのルートを追加。
2. **ラズパイ側**: `cloudflared`（webhook修正で既に導入済み）に対し、`cloudflared access ssh` 用の設定を追加。またはSSH configに以下を追加。
   ```
   Host raspi-home
       ProxyCommand cloudflared access ssh --hostname <access-hostname>
   ```
3. **Claude Code Remote環境側**: Service Token（Client ID/Secret）とSSH秘密鍵を環境変数/シークレットとして登録する。**リポジトリには絶対にコミットしない。**

## マーカーファイル規約（実装時）

- 置き場所: ラズパイ側 `MY_HOME_SYSTEM/logs/.claude_watch_marker`（`logs/` は `start_all.sh` が起動時に `mkdir -p` している既存ディレクトリ）。
- 内容: 前回チェック完了時刻のISO8601文字列のみ。
- 読み書きはSSHセッション内のシェルコマンドで完結させ、`home_system.service` 本体のコードには手を入れない（監視の追加が本体アプリの挙動に影響しないようにするため）。

## 異常検知の一次チェック基準（実装時のたたき台）

- `journalctl -u home_system.service --since <marker> -p err..emerg` に出力がある
- `logs/*.log` に前回マーカー以降で `ERROR` または `CRITICAL` を含む行がある
- ディスク使用率・メモリ使用率が閾値（要調整、例: 90%）を超えている
- 上記はいずれも `services/analysis_service.py` の `get_system_logs` / `get_disk_usage` / `get_memory_usage` と同種の取得方法（`journalctl`, `shutil.disk_usage`, `free -m`）に合わせる。ダッシュボード（`log_tab.py`）の実装と判定基準がズレないようにするため。

## 詳細調査〜改修案提示のガードレール（実装時に必ず守ること）

- コード修正の自動適用・自動デプロイは行わない（GitHub IssueまたはDraft PRとして提案するのみ）。
- `systemctl restart` 等の破壊的操作は自動実行しない（`log_tab.py` の再起動ボタンが確認チェックボックス必須になっているのと同じ思想）。
- 通知は `notification_service.py` が使うWebhook設定を再利用し、新たな認証情報経路を増やさない。
- 誤検知（flakyな一時的エラー等）が続く場合は、一次チェックの閾値・パターン側を見直す。詳細調査を毎回発火させる方向でのチューニングは行わない（コスト増につながるため）。

## 未実装の理由・次のステップ

上記「必要な準備」（Cloudflare Access設定、ラズパイ側cloudflared設定、環境シークレット登録）はいずれもユーザー側のインフラ操作が必要で、このセッションから直接実施できない。準備が整い次第、以下を実施して実装に進む。

1. Claude Code Remote の Routine（`create_trigger`）を作成し、上記フローのプロンプトを設定する。
2. 一次チェック〜詳細調査〜Issue/PR起票〜通知までの一連の動作を、実際に異常を模擬した状態で一度テストする。
3. 本ランブックに実装後の実際の設定値（Routineの`trigger_id`、チェック間隔等、機密情報を含まない範囲）を追記する。
