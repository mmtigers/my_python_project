# bounty_router.py (廃止)

## 廃止notice

このファイルが対応していたソース `MY_HOME_SYSTEM/routers/bounty_router.py` は、ギルド討伐依頼機能の廃止に伴い削除されました(2026-08 Family Quest大改修)。

- 廃止日: 2026-08
- 廃止理由: ファミリークエスト大改修に伴うギルド機能の廃止(ギルド討伐依頼関連テーブル `bounties` 等は既存データ保持のため削除していないが、アプリケーションコードからの参照はすべて削除済み)
- 詳細は[全体設計書.md](../全体設計書.md)の改訂メモおよびコミット `d1599d6`/`ffdc8c2`/`1818d5a` を参照。
- 本仕様書は削除されたソースの記録として残置する。新規の実装・参照の対象にはしないこと。

## 関連ドキュメント

- [quest_router.md](./quest_router.md) — 同じ2026-08のFamily Quest大改修で、ボス戦闘・装備・ファミリーマイレージ・週間ランキング等のエンドポイントも合わせて削除されたルーター。
- [GuildBoard.md](../family-quest/src/features/guild/components/GuildBoard.md) — 同じくギルド機能廃止に伴い削除された、フロントエンド側の対応コンポーネント（廃止notice）。
