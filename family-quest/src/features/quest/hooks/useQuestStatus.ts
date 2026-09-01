import { useMemo } from 'react';
import { User, Quest, QuestHistory } from '@/types';

interface UseQuestStatusProps {
    quest: Quest;
    currentUser: User;
    completedQuests: QuestHistory[];
    pendingQuests: QuestHistory[];
}

// ★共通化: 「このクエストは今ロック/完了/申請中か」を判定する純粋関数。
// 以前は useQuestStatus (このファイル)・QuestList.tsx のソート比較関数・App.tsx の
// クリックハンドラの3箇所にほぼ同じロジックが重複して実装されていた。
// フックの外（QuestList のソート処理など）からも使えるよう、プレーン関数として切り出す。
//
// #291: 元の3実装には qId の算出順序に食い違いがあった
// （useQuestStatus は `quest.quest_id || quest.id`、QuestList/App.tsx は `quest.id || quest.quest_id`）。
// quest.id はAPIから一度も送られてこない幽霊フィールドだったため型定義から削除し、
// ここではquest_idのみを参照する。
export interface QuestLockState {
    isLocked: boolean;
    isDone: boolean;
    isPending: boolean;
    isInfinite: boolean;
    /** 自分の（承認済み）完了履歴。無限クエストの周回数表示などに使う */
    myCompletions: QuestHistory[];
    pendingEntry?: QuestHistory;
    completedEntry?: QuestHistory;
}

export function getQuestLockState(
    quest: Quest,
    currentUser: User,
    completedQuests: QuestHistory[],
    pendingQuests: QuestHistory[]
): QuestLockState {
    const qId = quest.quest_id;

    // 無限クエスト判定（APIの型またはフロントエンド拡張フラグ）
    const isInfinite = quest.quest_type === 'infinite' || !!quest._isInfinite;

    // ▼ ロック判定ロジック (Smart Client方式)
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

    const pendingEntry = pendingQuests.find(pq =>
        pq.user_id === currentUser.user_id && pq.quest_id === qId
    );
    const isPending = !!pendingEntry;

    return {
        isLocked,
        isDone,
        isPending,
        isInfinite,
        myCompletions,
        pendingEntry,
        completedEntry: myCompletions[myCompletions.length - 1],
    };
}

export const useQuestStatus = ({ quest, currentUser, completedQuests, pendingQuests }: UseQuestStatusProps) => {
    const status = useMemo(() => {
        const { isLocked, isDone, isPending, isInfinite, myCompletions } =
            getQuestLockState(quest, currentUser, completedQuests, pendingQuests);

        const isRandom = quest.quest_type === 'random';
        const isLimited = quest.quest_type === 'limited';
        const isTimeLimited = !!quest.start_time;

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