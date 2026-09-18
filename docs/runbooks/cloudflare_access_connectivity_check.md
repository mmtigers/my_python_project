# Cloudflare Accessエッジ委譲の疎通確認手順(Issue #615)

Issue #321(2026-09-03決定)で、外部アクセス制御はアプリ層のJWT検証ではなく
エッジのCloudflare Accessに委譲する設計を正式採用した。この設計は
**オリジン(自宅サーバー)への直接到達がCloudflareのIPレンジ経由に限定されている**
というルーター/FW側の設定を前提とする。この前提が崩れると、
`ip_restriction_middleware`(ログのみでブロックしない)と`/webhook/switchbot`・
`/callback/line`・`/webhook/alexa`の無条件許可という設計は、Cloudflare Accessを
バイパスした無認証アクセスを許してしまう。

逆方向の失敗もある: これらのパスはエッジのCloudflare Access側で**バイパス対象に
設定されていないとWebhook自体がサーバーまで届かない**。Issue #517では
`/webhook/switchbot`・`/callback/line`が実際にブロックされており、SwitchBotの
実ペイロード調査(Issue #328)が長く進められない原因になっていた。
アプリ側の無条件許可パスの一覧は`unified_server.py`の`allowed_webhook_paths`
(現在は上記3件)が正で、エッジ側のバイパス設定と一致している必要がある。

**逆に、絶対にバイパスしてはならないパスもある**: `config.DASHBOARD_BASE_PATH`
(既定 `/dashboard`)は、Streamlitダッシュボード(`dashboard.py`、8501番)への
リバースプロキシである。ダッシュボードは認証機構を持たず、家族の健康記録・防犯ログの
閲覧と `sudo systemctl restart` ボタンを備えるため、8501番自体は 127.0.0.1 束縛のままにし、
外部からの到達をこの中継経由(= Cloudflare Accessの保護下)に一本化している。
ここをバイパス対象に設定すると、無認証でダッシュボードが外部公開される。
本runbookの手順4で定期的に確認すること。

この前提はコードからは検証できないため、以下の手順でオーナーが定期的に
(推奨: 四半期に1回、またはルーター/FW設定を変更した直後)確認すること。

## 1. オリジンのグローバルIPを確認する

自宅ルーターの管理画面、または以下で確認する:

```bash
curl -s https://ifconfig.me
```

## 2. オリジンのグローバルIP:ポートへ、Cloudflareを経由せず直接到達できないか確認する

自宅ネットワーク外の端末(スマートフォンのモバイル回線等、社内/自宅LANを経由しない環境)から:

```bash
# unified_server.pyが待ち受けるポート(デフォルト8000)への直接到達を試みる
curl -sv --max-time 5 http://<グローバルIP>:8000/ 2>&1 | head -20
```

**期待する結果**: 接続がタイムアウトする、または接続拒否される(ルーター/FWで
ブロックされている)。もし200番台や404等の応答が返ってきた場合、
オリジンへの直接到達が可能になっており、Cloudflare Access委譲の前提が
崩れている。

## 3. 前提が崩れていた場合の対応

- ルーター/FW設定で、該当ポートへの直接到達をCloudflareのIPレンジ
  (`https://www.cloudflare.com/ips/` で公開されている一覧)経由のみに
  制限し直す
- 対応後、本手順の2を再実行して塞がっていることを確認する
- 恒常的に直接到達を防げない構成の場合は、Issue #321の「案B」の前提が
  崩れているため、アプリ層でのJWT検証再導入(PR #80のrevert理由と
  再発防止策の検討)をオーナーとして再検討する

## 4. `/dashboard` がCloudflare Accessの保護下にあることを確認する

自宅ネットワーク外の端末(スマートフォンのモバイル回線等)から、公開ドメインの
`/dashboard` を開く:

```
https://<公開ドメイン>/dashboard
```

**期待する結果**: Cloudflare Accessのログイン画面(またはアクセス拒否)が表示される。
認証を通った後にダッシュボードが表示されるのが正常な状態。

**問題のある結果**: 認証を求められずにダッシュボードがそのまま表示される場合、
このパスがバイパス対象に設定されている。Zero Trust のポリシーからバイパス設定を
外すこと。応急処置としては、オリジン側で `DASHBOARD_PROXY_ENABLED=false` を
`.env` に設定して `home_system.service` を再起動すれば中継のルート自体が消える
(その間スマートフォンからは閲覧できなくなる)。

## 参照

- Issue #321(エッジ委譲を正式設計として確定した決定)
- Issue #615(本runbook作成の経緯)
- `docs/reports/CODE_REVIEW_REPORT_ALL.md` Critical#2/#8
- `MY_HOME_SYSTEM/unified_server.py` の `ip_restriction_middleware`
