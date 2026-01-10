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
        let variant: 'default' | 'completed' | 'pending' | 'infinite' | 'timeLimit' | 'random' | 'limited' = 'default';

        if (isDone) variant = 'completed';
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
            displayTitle,
            variant
        };
    }, [quest, currentUser, completedQuests, pendingQuests]);

    return status;
};