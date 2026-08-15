# EquipmentShop.tsx (廃止)

## 関連ドキュメント

- [ShopContainer.md](../ShopContainer.md) — 同じく削除済みの旧「お店」コンテナ。同ドキュメントの「現在の実装」節は「`App.tsx`が`EquipmentShop`/`RewardList`/`InventoryList`を直接importしレンダリングしている」と記載しているが、`App.md`の解析結果には`Equipment`関連の記述が見当たらない。EquipmentShop.md側の廃止notice（装備機能廃止に伴う削除）を踏まえると、ShopContainer.mdの当該記述はEquipmentShop廃止前の状態を記した内容のまま更新されていない可能性がある（あくまで両ドキュメントの記載内容を突き合わせた推測であり、実際のソースコードやコミット履歴は未確認）。
- [../../../../App.md](../../../../App.md) — 現在のメイン画面のコンポーネント一覧に`Equipment`関連のインポートが見当たらず、装備機能廃止後の状態と整合する。
- [types/index.md](../../../types/index.md) — 装備関連の型(`Equipment`, `OwnedEquipment`等)が装備機能廃止に伴い削除されている旨が明記されている。

## 廃止notice

このファイルが対応していたソース `family-quest/src/features/shop/components/EquipmentShop.tsx` は、装備機能の廃止に伴い削除されました(2026-08 Family Quest大改修)。

- 廃止日: 2026-08
- 廃止理由: 装備機能の廃止(装備購入・装着UIが不要となったため、アプリケーションコードからの参照はすべて削除済み)
- 本仕様書は削除されたソースの記録として残置する。新規の実装・参照の対象にはしないこと。
