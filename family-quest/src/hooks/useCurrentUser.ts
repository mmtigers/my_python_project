import { useEffect, useRef } from 'react';
import { User } from '@/types';
import { loadSavedUserId, saveCurrentUserId } from '@/lib/currentUserStorage';

// #393: usersが実データに揃ったら、保存済みuser_idをfindIndexで一度だけ解決する。
// 見つからなければ(メンバー削除・初回起動等)0番目にフォールバックする。
// また、以後のcurrentUserIdxの変化(ユーザー切替)を都度localStorageへ保存する。
// users.length <= 1 の間はまだ INITIAL_USERS のフォールバック(guest 1人)の可能性があり、
// それを保存してしまうと起動のたびに実データ到着前の一瞬で正しい保存値を消してしまうため、
// 解決・保存のどちらもスキップする。
//
// #552: currentUserIdx 自体のuseStateはCLAUDE.mdの記述どおりApp.tsxのトップレベル状態として
// 残し、このフックはその値の初期解決とlocalStorageへの永続化(=「現在ユーザー選択の状態」の
// 周辺ロジック)だけを引き受ける。
export function useCurrentUser(
    users: User[],
    currentUserIdx: number,
    setCurrentUserIdx: (idx: number) => void,
): void {
    // 起動時にlocalStorageへ保存されたuser_idを一度だけ解決するための保持先。
    // 実データ(users)が届くまではINITIAL_USERSの1人(guest)しか無く解決しようがないため、
    // 下のuseEffectで実データが揃うまで待つ。
    const pendingSavedUserIdRef = useRef<string | null>(loadSavedUserId());

    useEffect(() => {
        if (users.length <= 1) return;

        const savedUserId = pendingSavedUserIdRef.current;
        if (savedUserId !== null) {
            pendingSavedUserIdRef.current = null;
            const idx = users.findIndex(u => u.user_id === savedUserId);
            if (idx !== -1) {
                setCurrentUserIdx(idx);
                return; // 次のレンダーで本effectが再実行され、保存処理まで進む
            }
        }

        // メンバーが減った等でindexが範囲外になった場合は0番目にクランプする
        if (currentUserIdx >= users.length) {
            setCurrentUserIdx(0);
            return;
        }

        const user = users[currentUserIdx];
        if (user) saveCurrentUserId(user.user_id);
    }, [users, currentUserIdx, setCurrentUserIdx]);
}
