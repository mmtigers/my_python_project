import React, { useMemo } from 'react';
import { QuestActivityContext, QuestActivityValue } from './questActivityShared';

interface Props extends QuestActivityValue {
    children: React.ReactNode;
}

/**
 * 進行中のクエスト操作を配る Provider(#659)。App.tsx が唯一の使用者。
 *
 * 値は App の state/ref から来るので、ここでは組み立てて memo 化するだけ。
 * `completedSignal` は nonce 付きの新しいオブジェクトとして毎回作られるため、
 * 参照が変わったときだけ下流が再レンダーされる。
 */
export const QuestActivityProvider: React.FC<Props> = ({
    completedSignal, processingQuestKeys, busyHistoryIds, children,
}) => {
    const value = useMemo<QuestActivityValue>(
        () => ({ completedSignal, processingQuestKeys, busyHistoryIds }),
        [completedSignal, processingQuestKeys, busyHistoryIds],
    );

    return (
        <QuestActivityContext.Provider value={value}>
            {children}
        </QuestActivityContext.Provider>
    );
};
