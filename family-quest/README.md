# family-quest

家族向けクエスト管理アプリのフロントエンド(React + Vite + PWA)。
ビルド成果物 `dist/` は MY_HOME_SYSTEM の unified_server (:8000) が `/quest/` で配信する。

## デプロイ

`dist/` はディスク直読みで配信されるため、**ビルド完了 = デプロイ完了**(サーバー再起動不要)。

- 自動: リポジトリルートの `git pull` で family-quest に変更があると、post-merge フック(`.git/hooks/post-merge`)が `deploy.sh` を自動実行する
- 手動:

```bash
./deploy.sh
```

フックはローカル設定のため、リポジトリを clone し直した場合は `.git/hooks/post-merge` の再設置が必要
(family-quest の変更を検知して `bash family-quest/deploy.sh` を呼ぶだけの薄いスクリプト)。

## 開発

```bash
npm run dev      # 開発サーバー (HMR)
npm run build    # 本番ビルド → dist/
npm run lint     # ESLint
```
