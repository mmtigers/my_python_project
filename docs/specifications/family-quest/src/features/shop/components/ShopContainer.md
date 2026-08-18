# ShopContainer.tsx（廃止）

## 関連ドキュメント

- [EquipmentShop.md](./EquipmentShop.md) — 本ファイルが「現在の実装」として言及しているコンポーネントの一つ。ただし`EquipmentShop.md`自身の廃止noticeによれば、装備機能廃止(2026-08)に伴い`EquipmentShop.tsx`自体も別途削除されており、`../../../../App.md`（App.tsxの解析結果）にも`Equipment`関連の記述が見当たらない。本ファイルの「現在の実装」節の記述は、EquipmentShop廃止前の時点の内容のまま更新されていない可能性がある（ドキュメント同士の記載を突き合わせた限りでの指摘であり、実際のソースやコミット履歴は未確認）。
- [RewardList.md](./RewardList.md) — 本ファイルが「現在の実装」として言及している、購入可能な報酬一覧コンポーネント。
- [InventoryList.md](./InventoryList.md) — 本ファイルが「現在の実装」として言及している、所持品一覧コンポーネント。
- [RewardShop.md](./RewardShop.md) — `RewardList`と`InventoryList`を1画面に統合する現行の「ごほうび」画面コンテナ。ShopContainer.md本文には登場しないが、RewardShop.mdの解析結果によれば、以前独立していた「もちもの」タブ（本ファイルが担っていたタブ切替の一部）は廃止され、購入と所持品表示が`RewardShop`に統合されている。
- [../../../../App.md](../../../../App.md) — 「お店」画面を実際に描画している現在の呼び出し元。

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
