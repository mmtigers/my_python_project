import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/apiClient';
import { INITIAL_USERS, MASTER_QUESTS, MASTER_REWARDS } from '../lib/masterData';
import { User, Quest, QuestHistory, Reward, Equipment, Boss, QuestResult } from '@/types';

// 新規追加: any型を排除するための厳密なインターフェース定義
interface AdventureLog {
    id: string | number;
    message: string;
    created_at: string;
}

interface OwnedEquipment {
    id: string | number;
    equipment_id: string | number;
    user_id: string;
    equipped: boolean;
}

interface FamilyStats {
    total_quests: number;
    total_medals: number;
    level: number;
}

interface ChronicleItem {
    id: string | number;
    event_type: string;
    description: string;
    timestamp: string;
}

interface LevelUpInfo {
    user: string;
    level: number;
    job: string;
}

interface Bounty {
    id: string | number;
    title: string;
    reward: number;
    status: string;
}

interface PendingInventory {
    id: string | number;
    item_id: string | number;
    user_id: string;
    status: string;
}

// APIレスポンスの型定義
interface GameDataResponse {
    users: User[];
    quests: Quest[];
    rewards: Reward[];
    completedQuests: QuestHistory[];
    pendingQuests: QuestHistory[];
    logs: AdventureLog[];
    equipments: Equipment[];
    ownedEquipments: OwnedEquipment[];
    boss: Boss | null;
}

interface ChronicleResponse {
    stats: FamilyStats;
    chronicle: ChronicleItem[];
}

interface ApproveResponse {
    success: boolean;
    bossEffect?: any; // 必要に応じて厳密な型を定義
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

    // 管理用Mutation
    const adminUpdateBossMutation = useMutation({
        mutationFn: async (data: { maxHp?: number; currentHp?: number; isDefeated?: boolean }) => {
            return apiClient.post('/api/quest/admin/boss/update', {
                max_hp: data.maxHp,
                current_hp: data.currentHp,
                is_defeated: data.isDefeated
            });
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['gameData'] });
        },
    });

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

    // 追加: ペンディングインベントリの取得（無限ループ防止のための安全なポーリング）
    const { data: pendingInventory } = useQuery<PendingInventory[]>({
        queryKey: ['pendingInventory'],
        queryFn: () => apiClient.get('/api/quest/inventory/admin/pending'),
        refetchInterval: 1000 * 10,
        staleTime: 1000 * 5,
    });

    // 追加: バウンティリストの取得（無限ループ防止のための安全なポーリング）
    const { data: bounties } = useQuery<Bounty[]>({
        queryKey: ['bounties'],
        queryFn: () => apiClient.get('/api/bounties/list'),
        refetchInterval: 1000 * 15, // 15秒間隔
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
                history_id: history.id || history.history_id,
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
                history_id: history.id,
            });
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['gameData'] });
        },
        onError: (err) => handleError('承認', err),
    });

    // 却下
    const rejectQuestMutation = useMutation({
        mutationFn: async ({ user, history }: { user: User; history: QuestHistory }) => {
            return apiClient.post('/api/quest/reject', {
                approver_id: user.user_id,
                history_id: history.id,
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

    // 装備購入
    const buyEquipmentMutation = useMutation({
        mutationFn: async ({ user, item }: { user: User; item: Equipment }) => {
            return apiClient.post('/api/quest/equip/purchase', {
                user_id: user.user_id,
                equipment_id: item.id || item.equipment_id,
            });
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['gameData'] });
        },
        onError: (err) => handleError('装備購入', err),
    });

    // 装備変更
    const changeEquipmentMutation = useMutation({
        mutationFn: async ({ user, item }: { user: User; item: Equipment }) => {
            return apiClient.post('/api/quest/equip/change', {
                user_id: user.user_id,
                equipment_id: item.id || item.equipment_id,
            });
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['gameData'] });
        },
        onError: (err) => handleError('装備変更', err),
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
                earnedMedals: res.earnedMedals,
                leveledUp: res.leveledUp,
                bossEffect: res.bossEffect
            };
        } catch (e) {
            return { success: false, reason: 'error' };
        }
    };

    const cancelQuest = async (user: User, historyItem: QuestHistory) => {
        try {
            await cancelQuestMutation.mutateAsync({ user, history: historyItem });
            return { success: true };
        } catch (e) {
            return { success: false };
        }
    };

    const approveQuest = async (user: User, historyItem: QuestHistory) => {
        if (!['dad', 'mom'].includes(user.user_id)) return { success: false, reason: 'permission' };
        try {
            // any キャストを排除し、型安全なレスポンスを定義
            const res = await approveQuestMutation.mutateAsync({ user, history: historyItem }) as unknown as ApproveResponse;
            return {
                success: true,
                bossEffect: res?.bossEffect
            };
        } catch (e) { return { success: false }; }
    };

    const rejectQuest = async (user: User, historyItem: QuestHistory) => {
        if (!['dad', 'mom'].includes(user.user_id)) return { success: false, reason: 'permission' };
        try {
            await rejectQuestMutation.mutateAsync({ user, history: historyItem });
            return { success: true };
        } catch (e) { return { success: false }; }
    };

    // buyReward ラッパー
    const buyReward = async (user: User, reward: Reward) => {
        const cost = reward.cost_gold || reward.cost;
        if ((user.gold || 0) < cost) return { success: false, reason: 'gold' };

        try {
            const res = await buyRewardMutation.mutateAsync({ user, reward }) as unknown as PurchaseResponse;
            return { success: true, newGold: res.newGold, reward };
        } catch (e) { return { success: false, reason: 'error' }; }
    };

    const buyEquipment = async (user: User, item: Equipment) => {
        if ((user.gold || 0) < item.cost) return { success: false, reason: 'gold' };

        try {
            await buyEquipmentMutation.mutateAsync({ user, item });
            return { success: true, item };
        } catch (e) { return { success: false, reason: 'error' }; }
    };

    const changeEquipment = async (user: User, item: Equipment) => {
        try {
            await changeEquipmentMutation.mutateAsync({ user, item });
            return { success: true };
        } catch (e) { return { success: false }; }
    };

    const refreshData = () => {
        queryClient.invalidateQueries({ queryKey: ['gameData'] });
        queryClient.invalidateQueries({ queryKey: ['inventory'] }); // 全インベントリも強制再取得
    };

    const adminUpdateBoss = async (data: { maxHp?: number; currentHp?: number; isDefeated?: boolean }) => {
        try {
            await adminUpdateBossMutation.mutateAsync(data);
            return { success: true };
        } catch (e) {
            return { success: false };
        }
    };


    return {
        users: gameData?.users || INITIAL_USERS,
        quests: gameData?.quests || MASTER_QUESTS,
        rewards: gameData?.rewards || MASTER_REWARDS,
        completedQuests: gameData?.completedQuests || [],
        pendingQuests: gameData?.pendingQuests || [],
        adventureLogs: gameData?.logs || [],
        equipments: gameData?.equipments || [],
        ownedEquipments: gameData?.ownedEquipments || [],
        familyStats: chronicleData?.stats || null,
        chronicle: chronicleData?.chronicle || [],
        boss: gameData?.boss || null,
        pendingInventory: pendingInventory || [],
        bounties: bounties || [],
        isLoading: isGameDataLoading,

        completeQuest,
        approveQuest,
        rejectQuest,
        cancelQuest,
        buyReward,
        buyEquipment,
        changeEquipment,
        refreshData,
        adminUpdateBoss,
    };
};