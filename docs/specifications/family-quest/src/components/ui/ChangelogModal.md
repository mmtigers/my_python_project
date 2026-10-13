## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | ChangelogModal.tsx |
| 言語 | React (TypeScript) |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

* [./Modal.md](./Modal.md) - 土台として利用する汎用モーダル
* [../../lib/changelog.md](../../lib/changelog.md) - 表示する履歴データ`CHANGELOG`の公開元(データの正は`src/lib/changelog.json`で、`changelog.ts`が型付きで公開する)
* [../layout/Header.md](../layout/Header.md) - 開くためのボタン(右上)を持つ

## 2. ファイルの概要

* 更新履歴（バージョン・日付・変更点）を新しい順に一覧表示するモーダル。タイトルは「アップデートのれきし」。
* Props は `isOpen` / `onClose` のみ。内容は `CHANGELOG` を全件描画する（`max-h-[60vh]` でスクロール）。
* 履歴データの正は `src/lib/changelog.json`（`src/lib/changelog.ts` が型付きで公開する `CHANGELOG` を import する）。#933 でデータが `changelog.ts` から `changelog.json` へ移ったことに伴い、コメントのみ更新された（コンポーネントの挙動に変更はない）。根拠: [import] (行番号: 3 / 抜粋: "import { CHANGELOG } from '@/lib/changelog';")、[コメント] (行番号: 11)
* `App.tsx` が `lazy` で読み込み、ヘッダー右上のボタン(`onChangelogClick`)で開く。既読管理・自動ポップアップはない。
