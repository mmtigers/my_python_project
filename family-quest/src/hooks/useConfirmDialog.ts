import { useRef, useState } from 'react';
import { User } from '@/types';
import { ConfirmTarget } from '@/components/ui/ConfirmModal';

export type ConfirmMode = 'complete' | 'purchase' | 'reject';

// #552: 完了・購入・却下の確認モーダルまわりの状態(App.tsxの17個のuseStateのうち
// confirmMode/confirmTarget/confirmUser/rejectReason/isConfirmingの5つ)をまとめて
// 抱えるフック。実行そのもの(API呼び出し・メダル演出等)はApp.tsx側に残す
// (useGameDataの他の状態・handlerと密結合なため)。
export function useConfirmDialog() {
    // モーダル状態 (完了・購入・却下。取消は長押しでのみ発火するため確認を挟まない)
    const [confirmMode, setConfirmMode] = useState<ConfirmMode | null>(null);
    const [confirmTarget, setConfirmTarget] = useState<ConfirmTarget | null>(null);
    // クエスト完了/購入を実行する当人。横画面の4人表示では「今アクティブなユーザー」が
    // 存在しないため、どのパネルの操作かをここで明示的に持つ(承認/却下は別途「親」固定で扱う)。
    const [confirmUser, setConfirmUser] = useState<User | null>(null);
    const [rejectReason, setRejectReason] = useState<string | null>(null);
    // #101: 確認モーダルの「はい」連打による二重実行(例: 購入の二重成立)を防ぐガード。
    // レスポンス前の同期的な連打はstate更新の反映(再レンダー)を待たずに発生しうるため、
    // 判定にはuseState単独ではなくrefを使い、ボタンの見た目のdisabled/ローディング表示には
    // 対になるstateを使う。
    const [isConfirming, setIsConfirming] = useState(false);
    const isConfirmingRef = useRef(false);

    // 確認ダイアログを開く。既存のrejectReasonは新しいダイアログには持ち越さない。
    const openConfirm = (mode: ConfirmMode, target: ConfirmTarget, user: User | null = null) => {
        setConfirmMode(mode);
        setConfirmTarget(target);
        setConfirmUser(user);
        setRejectReason(null);
    };

    const closeConfirm = () => {
        setConfirmMode(null);
        setConfirmTarget(null);
        setConfirmUser(null);
        setRejectReason(null);
    };

    return {
        confirmMode,
        confirmTarget,
        confirmUser,
        rejectReason,
        setRejectReason,
        isConfirming,
        setIsConfirming,
        isConfirmingRef,
        openConfirm,
        closeConfirm,
    };
}
