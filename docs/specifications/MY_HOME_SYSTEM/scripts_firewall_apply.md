## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `scripts/firewall_apply.sh` |
| 言語 | Shell (bash) |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

* `MY_HOME_SYSTEM/deploy/systemd/home_firewall.service` / `MY_HOME_SYSTEM/deploy/systemd/README.md` - 本スクリプトを起動時に root で実行する oneshot ユニット(`ExecStop` で `--remove` を呼び切り戻す)
* [unified_server.md](./unified_server.md) - 制限対象のアプリ(0.0.0.0:8000)。`allowed_webhook_paths` と Cloudflare Access 委譲の設計
* [health_watch.md](./health_watch.md) - チェック7(実機構成ドリフト検知)が `deploy/systemd/*.service` と `/etc/systemd/system/` の一致を検査する

## 2. ファイルの概要

`unified_server.py` が待ち受ける 8000番への TCP 接続元を、loopback(127.0.0.0/8)・直結サブネット(実行時に `ip -o -4 route show scope link` から検出)・Tailscale(100.64.0.0/10)に限定する iptables ルールを適用する(2026-09-19 新設)。

経緯: 2026-09-19 の実機棚卸しで、ラズパイ本体にファイアウォールが一切無い(iptables の各チェーンが ACCEPT、ufw 未導入)ことを確認した。CLAUDE.md が Cloudflare Access 委譲の前提として挙げる「オリジンへの直接到達が限定されていること」が、ルーター設定だけに依存していたため、オーナー判断で 8000番を限定した。cloudflared はトンネル(外向き接続)で localhost:8000 に中継するため、外部からの Webhook(SwitchBot/LINE/Alexa)は loopback から届き、この制限の影響を受けない(uvicorn のアクセスログに外部 IP が `<ip>:0` で出るのは、既定で 127.0.0.1 からの転送ヘッダーだけを信用する uvicorn がそれを採用した印)。

* 根拠: 冒頭コメント (行番号: 2-28 / 抜粋: "# アプリポート(8000)の接続元制限 (firewall_apply.sh)")

## 3. 外部依存関係

| コマンド | 用途 |
| --- | --- |
| `iptables` | 専用チェーン `HOME_APP_8000` の作成・中身の積み直し、INPUT からの入口の追加/削除(環境変数 `IPTABLES` で差し替え可能) |
| `ip` | 直結サブネットの検出(環境変数 `IP_CMD` で差し替え可能) |

## 4. 主要要素の定義

### 引数

* 無し: ルールを適用する。何度実行しても同じ状態になる(チェーンは `-F` で空にしてから積み直し、INPUT の入口は `-C` で存在確認してから1本だけ追加)。
* `--remove`: INPUT の入口を全て外し、チェーンを空にして削除する(切り戻し)。ルールが無い状態で実行しても何もしない。

### チェーン `HOME_APP_8000` の中身(上から順に評価)

1. `-s 127.0.0.0/8 -j RETURN`(許可。INPUT の既定 ACCEPT へ戻る)
2. 直結サブネットごとに `-s <cidr> -j RETURN`(有線と無線が同じ LAN に直結している場合は重複を除いて1本)
3. `-s 100.64.0.0/10 -j RETURN`(Tailscale)
4. `-m limit --limit 5/min -j LOG --log-prefix "HOME_APP_8000 DROP: "`(想定外の遮断の調査用に kernel ログへ記録)
5. `-j DROP`

INPUT からは `-p tcp --dport 8000 -j HOME_APP_8000` の1本だけを先頭に挿入する。**8000番以外のポート(SSH・Samba 等)には一切触れない。**

### fail-open

直結サブネットを1つも検出できない場合(起動直後でネットワークが未確立等)は、家族の端末を締め出す事故を避けるため、既存の制限も外したうえで制限を掛けずに正常終了する。

* 根拠: `if [ "${#LAN_CIDRS[@]}" -eq 0 ]; then` ブロック

### 設計上の注意

* LAN のアドレスはリポジトリに書かない方針(Issue #663)のため、許可するサブネットはハードコードせず実行時に検出する。
* スクリプトはリポジトリ内(一般ユーザーが書き込める場所)にあり、root で実行される。ラズパイの運用ユーザーはもともとパスワード無しの sudo を持つため、新たな権限境界は生じない。

## 5. テスト

`tests/test_firewall_apply_sh.py` が、呼び出しを記録し状態を持つ `iptables`/`ip` のスタブで実行して検証する(実機の iptables には触れない): 許可/遮断の順序、8000番だけに入口を作ること、再実行の冪等性、直結サブネットが無いときの fail-open(既存の制限も外す)、`--remove` による切り戻し、ルールが無いときの `--remove`。
