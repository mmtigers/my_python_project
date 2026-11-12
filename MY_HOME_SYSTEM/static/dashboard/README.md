# static/dashboard

ダッシュボード(`routers/dashboard_router.py`)が配信する CSS・JS。配信できるのは
`services/dashboard_page_service.py` の `STATIC_ASSET_FILES`(許可リスト)にあるファイルだけ。

| ファイル | 役割 |
| --- | --- |
| `dashboard.css` | 全ページ共通のCSS(デザイントークン・ダークモード) |
| `app.js` | 全ページ共通。表示テーマの切替、折りたたみ内の `#id` を開く、Service Worker の登録 |
| `sw.js` | サーバーに繋がらないときの案内画面を返す Service Worker(`/dashboard/sw.js` で配信) |
| `camera.js` | 見守りページのライブ映像(タップしたカメラだけ再生・接続できないときの表示) |
| `maintenance.js` / `git_status.js` | システムページ |
| `hls.light.min.js` | [hls.js](https://github.com/video-dev/hls.js) 1.5.20 の light ビルド(npm の `hls.js@1.5.20` の `dist/hls.light.min.js` をそのまま置いたもの。Apache-2.0、`hls.LICENSE.txt`) |

## hls.js を更新するとき

CDN から読むのをやめて同梱している(インターネットが切れても LAN 内でライブ映像を見られるようにするため)。
手で編集せず、npm の配布物で置き換える:

```bash
npm pack hls.js@<版> && tar xzf hls.js-<版>.tgz
cp package/dist/hls.light.min.js MY_HOME_SYSTEM/static/dashboard/hls.light.min.js
cp package/LICENSE MY_HOME_SYSTEM/static/dashboard/hls.LICENSE.txt
```

置き換えたらこの表の版を直す。ESLint(`npm run lint:dashboard`)は `*.min.js` を対象外にしている。
