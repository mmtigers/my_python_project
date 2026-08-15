import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/apiClient';
import { INITIAL_USERS, MASTER_QUESTS, MASTER_REWARDS } from '../lib/masterData';
import { User, Quest, QuestHistory, Reward, QuestResult, PendingInventory } from '@/types';

// 新規追加: any型を排除するための厳密なインターフェース定義
interface AdventureLog {
    id: string | number;
    message: string;
    created_at: string;
}

// 家族全体の統計情報 (UserService.get_family_chronicle の "stats" レスポンスに対応)
export interface FamilyStats {
    totalLevel: number;
    totalGold: number;
    totalQuests: number;
    partyRank: string;
}

// 年代記の1エントリ (UserService._fetch_full_adventure_logs のレスポンスに対応。
// FamilyLog.tsx 側で複数の代替フィールド名にも防御的にフォールバックしているため、
// それらも任意プロパティとして許容する)
export interface ChronicleItem {
    type?: string;
    timestamp?: string;
    dateStr?: string;
    date?: string;
    id?: string | number;
    userName?: string;
    userAvatar?: string;
    avatar_url?: string;
    title?: string;
    text?: string;
    message?: string;
    quest_title?: string;
    gold?: number;
    reward_gold?: number;
    exp?: number;
    reward_exp?: number;
    created_at?: string;
}

export interface LevelUpInfo {
    user: string;
    level: number;
    job: string;
}

// APIレスポンスの型定義
interface GameDataResponse {
    users: User[];
    quests: Quest[];
    rewards: Reward[];
    completedQuests: QuestHistory[];
    pendingQuests: QuestHistory[];
    logs: AdventureLog[];
}

interface ChronicleResponse {
    stats: FamilyStats;
    chronicle: ChronicleItem[];
}

interface PurchaseResponse {
    newGold: number;
    success: boolean;
}

export const useGameData = (onLevelUp?: (info: LevelUpInfo) => void) => {
    const queryClient = useQueryClient();

    const handleError = (actionName: string, error: unknown) => {
        console.error(`${actionName} failed:`, error);
    };

    // apiClient側でスローされるErrorのmessageには、バックエンドが返す
    // {"detail": "..."} の内容が入っている（apiClient.ts参照）。
    // ここでそれを取り出し、呼び出し元(App.tsx)がユーザーに実際のエラー内容を
    // 表示できるようにする。
    const extractErrorDetail = (error: unknown): string | undefined => {
        return error instanceof Error ? error.message : undefined;
    };

    // 1. メインデータの取得
    const { data: gameData, isLoading: isGameDataLoading } = useQuery<GameDataResponse>({
        queryKey: ['gameData'],
        queryFn: () => apiClient.get('/api/quest/data'),
        staleTime: 1000 * 30,
        refetchInterval: 1000 * 10, // 10秒に1回のポーリングに制限
    });

    // 2. 年代記データの取得
    const { data: chronicleData } = useQuery<ChronicleResponse>({
        queryKey: ['chronicle'],
        queryFn: () => apiClient.get('/api/quest/family/chronicle'),
        staleTime: 1000 * 60 * 5,
    });

    // 承認待ちインベントリの取得（無限ループ防止のための安全なポーリング）
    // ★このクエリがアプリ内で唯一の登録元。ApprovalList側では独自クエリを持たず、
    // ここから props で受け取る（重複登録の解消）。
    const { data: pendingInventory } = useQuery<PendingInventory[]>({
        queryKey: ['pendingInventory'],
        queryFn: () => apiClient.fetchPendingInventory(),
        refetchInterval: 1000 * 10,
        staleTime: 1000 * 5,
    });

    // --- Actions (Mutations) ---


    // クエスト完了
    const completeQuestMutation = useMutation({
        mutationFn: async ({ user, quest }: { user: User; quest: Quest }) => {
            return apiClient.post<QuestResult>('/api/quest/complete', { // 型指定
                user_id: user.user_id,
                quest_id: quest.id || quest.quest_id,
            });
        },
        onSuccess: (res, variables) => {
            queryClient.invalidateQueries({ queryKey: ['gameData'] });
            // res は QuestResult 型になるためアクセス可能
            if (res.leveledUp && onLevelUp) {
                onLevelUp({
                    user: variables.user.name,
                    level: res.newLevel,
                    job: variables.user.job_class || '無職' // または 'ノービス', '' など
                });
            }
        },
        onError: (err) => handleError('クエスト完了', err),
    });

    // クエストキャンセル
    const cancelQuestMutation = useMutation({
        mutationFn: async ({ user, history }: { user: User; history: QuestHistory }) => {
            return apiClient.post('/api/quest/quest/cancel', {
                user_id: user.user_id,
                history_id: history.id ?? history.history_id,
            });
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['gameData'] });
        },
        onError: (err) => handleError('キャンセル', err),
    });

    // 承認
    const approveQuestMutation = useMutation({
        mutationFn: async ({ user, history }: { user: User; history: QuestHistory }) => {
            return apiClient.post('/api/quest/approve', {
                approver_id: user.user_id,
                history_id: history.id ?? history.history_id,
            });
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['gameData'] });
        },
        onError: (err) => handleError('承認', err),
    });

    // 却下
    const rejectQuestMutation = useMutation({
        mutationFn: async ({ user, history, reason }: { user: User; history: QuestHistory; reason?: string }) => {
            return apiClient.post('/api/quest/reject', {
                approver_id: user.user_id,
                history_id: history.id ?? history.history_id,
                reason,
            });
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['gameData'] });
        },
        onError: (err) => handleError('却下', err),
    });

    // 報酬購入
    const buyRewardMutation = useMutation({
        mutationFn: async ({ user, reward }: { user: User; reward: Reward }) => {
            return apiClient.post('/api/quest/reward/purchase', {
                user_id: user.user_id,
                reward_id: reward.id || reward.reward_id,
            });
        },
        onSuccess: (_data, variables) => { // data -> _data
            queryClient.invalidateQueries({ queryKey: ['gameData'] });
            queryClient.invalidateQueries({ queryKey: ['inventory', variables.user.user_id] });
        },
        onError: (err) => handleError('購入', err),
    });

    // --- ラッパー関数 (Async/Await対応) ---

    const completeQuest = async (user: User, quest: Quest) => {
        const qId = quest.id || quest.quest_id;
        const isPending = gameData?.pendingQuests.some(pq => pq.user_id === user.user_id && pq.quest_id === qId);

        if (isPending) {
            return { success: false, reason: 'pending' };
        }

        try {
            // QuestResult型として受け取る
            const res = await completeQuestMutation.mutateAsync({ user, quest });
            return {
                success: true,
                // ★バグ修正: 以前は status/message を返り値から落としていたため、
                // 子供が申請したクエスト（承認待ち）でも「申請完了」メッセージが
                // App.tsx 側で絶対に表示されなかった（res.status が常に undefined）。
                status: res.status,
                message: res.message,
                earnedMedals: res.earnedMedals,
                leveledUp: res.leveledUp,
            };
        } catch (e) {
            return { success: false, reason: 'error', detail: extractErrorDetail(e) };
        }
    };

    const cancelQuest = async (user: User, historyItem: QuestHistory) => {
        try {
            await cancelQuestMutation.mutateAsync({ user, history: historyItem });
            return { success: true };
        } catch (e) {
            return { success: false, reason: 'error', detail: extractErrorDetail(e) };
        }
    };

    const approveQuest = async (user: User, historyItem: QuestHistory) => {
        if (user.role !== 'role_adult') return { success: false, reason: 'permission' };
        try {
            await approveQuestMutation.mutateAsync({ user, history: historyItem });
            return { success: true };
        } catch (e) {
            return { success: false, reason: 'error', detail: extractErrorDetail(e) };
        }
    };

    const rejectQuest = async (user: User, historyItem: QuestHistory, rejectReason?: string) => {
        if (user.role !== 'role_adult') return { success: false, reason: 'permission' };
        try {
            await rejectQuestMutation.mutateAsync({ user, history: historyItem, reason: rejectReason });
            return { success: true };
        } catch (e) {
            return { success: false, reason: 'error', detail: extractErrorDetail(e) };
        }
    };

    // buyReward ラッパー
    const buyReward = async (user: User, reward: Reward) => {
        const cost = reward.cost_gold || reward.cost;
        if ((user.gold || 0) < cost) return { success: false, reason: 'gold' };

        try {
            const res = await buyRewardMutation.mutateAsync({ user, reward }) as unknown as PurchaseResponse;
            return { success: true, newGold: res.newGold, reward };
        } catch (e) {
            return { success: false, reason: 'error', detail: extractErrorDetail(e) };
        }
    };

    const refreshData = () => {
        queryClient.invalidateQueries({ queryKey: ['gameData'] });
        queryClient.invalidateQueries({ queryKey: ['inventory'] }); // 全インベントリも強制再取得
    };

    return {
        users: gameData?.users || INITIAL_USERS,
        quests: gameData?.quests || MASTER_QUESTS,
        rewards: gameData?.rewards || MASTER_REWARDS,
        completedQuests: gameData?.completedQuests || [],
        pendingQuests: gameData?.pendingQuests || [],
        adventureLogs: gameData?.logs || [],
        familyStats: chronicleData?.stats || null,
        chronicle: chronicleData?.chronicle || [],
        pendingInventory: pendingInventory || [],
        isLoading: isGameDataLoading,

        completeQuest,
        approveQuest,
        rejectQuest,
        cancelQuest,
        buyReward,
        refreshData,
    };
};
