# ShopContainer.tsx（廃止）

`family-quest/src/features/shop/components/ShopContainer.tsx` は削除された。

## 廃止理由

コミット `74e5f83`「fix(family-quest): add build config, fix critical/high findings」にて、
デッドコードだった `ShopContainer.tsx` が削除された（コミットメッセージ内 "Remove dead
ShopContainer.tsx and a stray .lnk file leaking an internal IP" を参照）。

## 現在の実装

「お店」「もちもの」タブの切り替えUIは `ShopContainer` に集約されていたのではなく、
呼び出し元の `family-quest/src/App.tsx` が `EquipmentShop` / `RewardList` /
`InventoryList` の各コンポーネントを直接importし、レンダリングしている。

* `family-quest/src/features/shop/components/EquipmentShop.tsx`
* `family-quest/src/features/shop/components/RewardList.tsx`
* `family-quest/src/features/shop/components/InventoryList.tsx`

これらの仕様は、それぞれ対応する `docs/specifications/family-quest/src/features/shop/components/EquipmentShop.md` /
`RewardList.md` / `InventoryList.md` を参照。
