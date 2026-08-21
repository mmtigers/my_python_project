# ShopContainer.tsx（廃止）

## 関連ドキュメント

- [EquipmentShop.md](./EquipmentShop.md) — かつて「現在の実装」として言及していたコンポーネントの一つ。装備機能廃止(2026-08)に伴い`EquipmentShop.tsx`自体も別途削除されており、現在の`App.tsx`では参照されていない。
- [RewardList.md](./RewardList.md) — 現在`RewardShop`経由で描画される、購入可能な報酬一覧コンポーネント。
- [InventoryList.md](./InventoryList.md) — 本ファイルが「現在の実装」として言及している、所持品一覧コンポーネント。
- [RewardShop.md](./RewardShop.md) — `RewardList`と所持ゴールド表示を1画面にまとめる現行の「ごほうび」画面コンテナ。以前独立していた「もちもの」タブ（本ファイルが担っていたタブ切替の一部）は廃止され、購入UIが`RewardShop`に統合されている。
- [../../../../App.md](../../../../App.md) — 「お店」画面を実際に描画している現在の呼び出し元。

`family-quest/src/features/shop/components/ShopContainer.tsx` は削除された。

## 廃止理由

コミット `74e5f83`「fix(family-quest): add build config, fix critical/high findings」にて、
デッドコードだった `ShopContainer.tsx` が削除された（コミットメッセージ内 "Remove dead
ShopContainer.tsx and a stray .lnk file leaking an internal IP" を参照）。

## 現在の実装

「お店」「もちもの」タブの切り替えUIは `ShopContainer` に集約されていたのではなく、
呼び出し元の `family-quest/src/App.tsx` が `RewardShop` / `InventoryList` の各コンポーネントを
直接importし、レンダリングしている（2026-08時点、`family-quest/src/App.tsx`のimport文で確認）。
本節はShopContainer廃止直後（装備機能廃止前）の状態を記していたが、その後の装備機能廃止
(2026-08 Family Quest大改修)により`EquipmentShop`/`RewardList`の直接呼び出しは
`RewardShop`経由の呼び出しに置き換わっており、本節はその変更を反映して更新した。

* `family-quest/src/features/shop/components/RewardShop.tsx`
* `family-quest/src/features/shop/components/InventoryList.tsx`

これらの仕様は、それぞれ対応する `docs/specifications/family-quest/src/features/shop/components/RewardShop.md` /
`InventoryList.md` を参照。
