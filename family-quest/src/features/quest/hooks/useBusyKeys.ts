import { useRef, useState } from 'react';

/**
 * 「送信中のキー集合」を ref と state の二重で持つためのフック(#659)。
 *
 * App.tsx には同じ形の二重化が2つ書かれていた:
 *   - 承認中の history id (`approvingHistoryIds`、#391 F-L8)
 *   - 完了/取消が送信中の (user_id, quest_id) キー (`processingQuestKeys`、#391)
 *
 * どちらも**判定は同期的な ref で、見た目だけ state に追従させる**という #101 以来の
 * パターンである。連打はレンダーの反映を待たずに起きるため、`useState` の値で
 * 「もう送信中か」を判定すると1回目の state 更新が反映される前に2回目が通ってしまう。
 * かといって ref だけでは再レンダーが起きず、ボタンのローディング表示が出ない。
 *
 * 判定用の `ref` と表示用の `keys` がずれないよう、`sync()` を呼ぶ責務は
 * 呼び出し側に残してある(複数キーをまとめて登録してから1回だけ同期したい、
 * という既存の使い方 — `handleApproveAll` — を壊さないため)。
 */
export interface BusyKeys<T> {
    /** 判定用。再レンダーを待たずに読み書きできる(常に Set が入っており null にならない)。 */
    ref: { current: Set<T> };
    /** 表示用。`sync()` を呼んだ時点の `ref` の内容。 */
    keys: T[];
    /** `ref` の現在値を `keys` へ写して再レンダーさせる。 */
    sync: () => void;
}

export function useBusyKeys<T>(): BusyKeys<T> {
    const ref = useRef<Set<T>>(new Set());
    const [keys, setKeys] = useState<T[]>([]);
    const sync = () => setKeys([...ref.current]);
    return { ref, keys, sync };
}
