import { useMemo } from 'react';
import { User, Quest, QuestHistory } from '@/types';

interface UseQuestStatusProps {
    quest: Quest;
    currentUser: User;
    completedQuests: QuestHistory[];
    pendingQuests: QuestHistory[];
}

export const useQuestStatus = ({ quest, currentUser, completedQuests, pendingQuests }: UseQuestStatusProps) => {
    const status = useMemo(() => {
        const qId = quest.quest_id || quest.id;
        // 無限クエスト判定（APIの型またはフロントエンド拡張フラグ）
        const isInfinite = quest.type === 'infinite' || quest.quest_type === 'infinite' || quest._isInfinite;
        const isRandom = quest.type === 'random';
        const isLimited = quest.type === 'limited';
        const isTimeLimited = !!quest.start_time;

        // ▼ 追加: ロック判定ロジック (Smart Client方式)
        // 1. 前提クエストIDがあるか確認
        const preReqId = quest.pre_requisite_quest_id;

        // 2. 前提条件の達成確認
        // completedQuests には「今日」の承認済みデータのみが入っている前提 (GameSystem仕様)
        const isPreReqCleared = !preReqId || completedQuests.some(cq =>
            cq.user_id === currentUser.user_id &&
            cq.quest_id === preReqId &&
            cq.status === 'approved'
        );

        // 3. ロック状態の確定 (前提未達成ならロック)
        const isLocked = !isPreReqCleared;

        // 自分の完了履歴
        const myCompletions = completedQuests.filter(cq =>
            cq.user_id === currentUser.user_id &&
            cq.quest_id === qId &&
            cq.status === 'approved'
        );

        // 状態判定
        let isDone = myCompletions.length > 0;
        if (isInfinite) isDone = false; // 無限なら未完了扱い

        const isPending = pendingQuests.some(pq =>
            pq.user_id === currentUser.user_id && pq.quest_id === qId
        );

        // 表示タイトルの生成
        let displayTitle = quest.title;
        if (isInfinite) {
            const count = myCompletions.length + 1;
            displayTitle = `${quest.title} (${count}回目)`;
        }

        // カードのバリエーション決定
        let variant: 'default' | 'completed' | 'pending' | 'infinite' | 'timeLimit' | 'random' | 'limited' | 'locked' = 'default';

        if (isLocked) variant = 'locked'; // ▼ ロックを最優先で判定
        else if (isDone) variant = 'completed';
        else if (isPending) variant = 'pending';
        else if (isInfinite) variant = 'infinite';
        else if (isTimeLimited) variant = 'timeLimit';
        else if (isRandom) variant = 'random';
        else if (isLimited) variant = 'limited';

        return {
            isDone,
            isPending,
            isInfinite,
            isRandom,
            isTimeLimited,
            isLimited,
            isLocked,
            displayTitle,
            variant
        };
    }, [quest, currentUser, completedQuests, pendingQuests]);

    return status;
};