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

実装再開時にこの3点が完了している必要がある。UIはCloudflare側の更新で変わる可能性があるため、実際の画面に合わせて読み替えること。

### 1. Cloudflare Zero Trust側

**前提確認**: `start_all.sh` の `switchbot_webhook_fix.py` が使っている既存の `cloudflared` トンネルが、**名前付きトンネル（named tunnel）** であること。もし `cloudflared tunnel --url ...` のような使い捨てURL方式（quick tunnel）であれば、先に名前付きトンネルへ移行する必要がある（`cloudflared tunnel create <name>` でトンネルを作成し、DNSレコードを紐付け直す）。

1. https://one.dash.cloudflare.com/ にログインし、対象アカウントのZero Trustダッシュボードを開く。
2. 左メニュー **Networks → Tunnels** を開き、既存トンネル（webhook用に使っているもの）を選択し、トンネルIDを控える。
3. トンネルの **Public Hostname** タブで「Add a public hostname」を押し、以下を設定して保存する。
   - Subdomain: 例 `ssh-raspi`
   - Domain: 既存で使っているドメイン
   - Type: `SSH`
   - URL: `localhost:22`
4. 左メニュー **Access → Applications → Add an application → Self-hosted** を選び、以下を設定する。
   - Application name: 例 `Raspi SSH (Claude monitoring)`
   - Application domain: 手順3で作った `ssh-raspi.<domain>`
   - Session Duration: 用途に合わせて設定（自動化専用なので長め、例24時間でも可）
5. Policyを1つ追加する。
   - Policy name: 例 `Claude Automation`
   - Action: `Service Auth`
   - Rule: `Include` → `Service Token` を選択（手順6で発行するトークンをここで指定するため、一旦保存だけしておき後で紐付けてもよい）
6. 左メニュー **Access → Service Tokens → Create Service Token** を開き、以下を設定する。
   - Service Token Name: 例 `claude-pi-monitor`
   - Duration: 有効期限（無期限にしない場合は、後述の運用注意点を参照）
   - 発行後に表示される **Client ID** と **Client Secret** を控える（**Client Secretはこの画面でしか表示されない**ので、必ずこのタイミングでメモする）。
7. 手順5のPolicyに戻り、Includeルールとして手順6のService Tokenを紐付ける。

### 2. ラズパイ側

1. `cloudflared --version` で既にインストール済みであることを確認する（webhook修正で使用中のはず）。
2. `sudo systemctl status ssh` でSSHデーモンが起動していることを確認する。
3. トンネルの設定ファイル（`/etc/cloudflared/config.yml` が一般的。環境によっては `~/.cloudflared/config.yml`）に、SSH用のingressルールを追加する。**既存のWebhook/API用ルールより前に**書くこと（cloudflaredのingressは上から順にマッチする）。
   ```yaml
   tunnel: <既存のトンネルID>
   credentials-file: /etc/cloudflared/<既存のトンネルID>.json
   ingress:
     - hostname: ssh-raspi.example.com     # 追加: SSH用
       service: ssh://localhost:22
     - hostname: home-system.example.com   # 既存: Webhook/API用
       service: http://localhost:8000
     - service: http_status:404
   ```
4. `sudo systemctl restart cloudflared` を実行し、設定を反映する。
5. `sudo systemctl status cloudflared` でエラーが出ていないことを確認する。

### 3. Claude Code Remote環境側

1. 環境変数/シークレットとして以下を登録する（Claude Code Remoteの環境設定画面）。**リポジトリには絶対にコミットしない。**
   - `CF_ACCESS_CLIENT_ID` — 手順1-6のClient ID
   - `CF_ACCESS_CLIENT_SECRET` — 手順1-6のClient Secret
   - `RASPI_SSH_HOSTNAME` — 例 `ssh-raspi.example.com`
   - `RASPI_SSH_USER` — ラズパイ側のSSHログインユーザー名
   - `RASPI_SSH_PRIVATE_KEY` — ラズパイの `~/.ssh/authorized_keys` に対応する秘密鍵（既存の鍵を流用せず、この自動化専用の鍵ペアを新規発行し、ラズパイ側に公開鍵を追加登録することを推奨）
2. SSH接続時、`cloudflared access ssh` はService TokenをCloudflare公式仕様の環境変数 `TUNNEL_SERVICE_TOKEN_ID` / `TUNNEL_SERVICE_TOKEN_SECRET` から読む（`CF_ACCESS_CLIENT_ID`/`CF_ACCESS_CLIENT_SECRET` をこの名前にマッピングして渡す）。SSH configの例:
   ```
   Host raspi-home
       HostName ssh-raspi.example.com
       User <RASPI_SSH_USERの値>
       IdentityFile ~/.ssh/claude_raspi_monitor
       ProxyCommand sh -c 'TUNNEL_SERVICE_TOKEN_ID=$CF_ACCESS_CLIENT_ID TUNNEL_SERVICE_TOKEN_SECRET=$CF_ACCESS_CLIENT_SECRET cloudflared access ssh --hostname %h'
   ```
3. 動作確認: `ssh raspi-home 'echo ok'` が認証エラーなく `ok` を返すことを確認してから、Routine作成に進む。

### Service Tokenの有効期限に関する注意

Cloudflare Service Tokenに有効期限を設定した場合、期限切れでRoutineのSSH接続が失敗するようになる。無期限にするか、期限を設定する場合はカレンダーリマインダー等で更新時期を管理すること。更新時は「Client ID」は変わらず「Client Secret」のみ再発行されるため、CCR環境側の `CF_ACCESS_CLIENT_SECRET` のみ更新すればよい。

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
