# gameHelpers.js (廃止)

## 廃止notice

このファイルが対応していたソース `family-quest/src/utils/gameHelpers.js` は削除されました(コミット `690c941`「死にコードgameHelpers.js削除・購入確認のcost_gold欠落対応」)。

- 廃止日: 2026-08
- 廃止理由: 本仕様書の旧版の「9. 不明事項一覧」で指摘されていた通り、`DAYS`/`getDayIndex`/`getCurrentTime`/`getNextLevelExp`はいずれもfamily-quest内のどこからもインポートされていない完全な死にコードだった。加えて`getDayIndex`は日曜日=0とする曜日インデックスを返す実装だが、アプリ内の他の曜日関連実装は月曜日=0を規約としており、将来誤って利用された場合に不整合を招く「罠」であったため削除された。同コミットでは併せて`App.tsx`の購入確認モーダルの`cost_gold`欠落表示(`undefinedG`)も修正されている。
- 本仕様書は削除されたソースの記録として残置する。新規の実装・参照の対象にはしないこと。

## 関連ドキュメント

- [../../../MY_HOME_SYSTEM/game_logic.md](../../../MY_HOME_SYSTEM/game_logic.md) - `getNextLevelExp`と同一の経験値計算式（`100 × 1.2^(level-1)`の切り捨て）を実装していたPython側`GameLogic.calculate_next_level_exp`。本ファイル廃止後もPython側の実装は存続しており、2言語間の重複実装ではなくなった。
- [../../App.md](../../App.md) - `cost_gold`欠落時に`undefinedG`と表示されていた購入確認モーダルの修正箇所(本ファイル削除と同一コミット)。
