// #660: 画面間で共有する UI の閾値・間隔を1箇所に集約する。
// 以前は同じ数値が App.tsx・FamilyLog.tsx・ApprovalList.tsx に直書きで散っており、
// 「切り替えの感度を変える」ような調整が片方だけに入る状態だった。

/**
 * 画面(ユーザー/タブ)を切り替えるスワイプと判定する横方向の移動量(px)。
 * 承認リストの行スワイプは、誤操作が即時の承認・却下につながるため別途
 * 大きめの閾値(`APPROVAL_SWIPE_THRESHOLD_PX`)を使う。
 */
export const VIEW_SWIPE_THRESHOLD_PX = 60;

/** 承認/却下のスワイプと判定する横方向の移動量(px)。誤操作の代償が大きいので広めに取る。 */
export const APPROVAL_SWIPE_THRESHOLD_PX = 90;

/**
 * インベントリ一覧のポーリング間隔(ミリ秒)。
 * 他のクエリ(gameData 10秒 / chronicle 60秒)より短い5秒だったため、横画面で4パネルを
 * 常時表示するキオスク端末では毎分48リクエストの上乗せになっていた。所持アイテムは
 * 自分の操作以外で増減しない(購入・使用時は invalidateQueries が走る)ので、
 * 取りこぼしの保険としては15秒で足りる。
 */
export const INVENTORY_POLL_INTERVAL_MS = 15000;
