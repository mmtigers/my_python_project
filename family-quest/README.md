# family-quest

家族向けクエスト管理アプリのフロントエンド(React + Vite + PWA)。
ビルド成果物 `dist/` は MY_HOME_SYSTEM の unified_server (:8000) が `/quest/` で配信する。

## デプロイ

`dist/` はディスク直読みで配信されるため、**ビルド完了 = デプロイ完了**(サーバー再起動不要)。

- 自動: リポジトリルートの `git pull` で family-quest に変更があると、post-merge フック(リポジトリ管理の `deploy/git-hooks/post-merge`)が `deploy.sh --if-stale` を自動実行する
- 手動:

```bash
./deploy.sh
```

フックは `MY_HOME_SYSTEM/start_all.sh` がサーバー起動のたびに `git config core.hooksPath deploy/git-hooks` で
冪等に登録するため、clone し直しても手動の再設置は不要(詳細は [deploy/git-hooks/README.md](../deploy/git-hooks/README.md))。

## 開発

```bash
npm run dev      # 開発サーバー (HMR)
npm run build    # 本番ビルド → dist/
npm run lint     # ESLint
```
