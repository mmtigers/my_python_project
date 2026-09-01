# EquipmentShop.tsx (廃止)

## 廃止notice

このファイルが対応していたソース `family-quest/src/features/shop/components/EquipmentShop.tsx` は、装備機能の廃止に伴い削除されました(2026-08 Family Quest大改修)。

- 廃止日: 2026-08
- 廃止理由: 装備機能の廃止(装備購入・装着UIが不要となったため、アプリケーションコードからの参照はすべて削除済み)
- 詳細は[全体設計書.md](../../../../../全体設計書.md)の改訂メモおよびコミット `d1599d6`/`ffdc8c2`/`1818d5a` を参照。
- 本仕様書は削除されたソースの記録として残置する。新規の実装・参照の対象にはしないこと。

## 関連ドキュメント

- [ShopContainer.md](./ShopContainer.md) — 同じく削除済みの旧「お店」コンテナ。「現在の実装」節は装備機能廃止後の状態(`App.tsx`が`RewardShop`/`InventoryList`を直接importしレンダリングしている)に更新済み。
- [../../../../App.md](../../../../App.md) — 現在のメイン画面のコンポーネント一覧に`Equipment`関連のインポートが見当たらず、装備機能廃止後の状態と整合する。
- [types/index.md](../../../types/index.md) — 装備関連の型(`Equipment`, `OwnedEquipment`等)が装備機能廃止に伴い削除されている旨が明記されている。
