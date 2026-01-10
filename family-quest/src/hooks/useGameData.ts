import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/apiClient';
import { INITIAL_USERS, MASTER_QUESTS, MASTER_REWARDS } from '../lib/masterData';
import { User, Quest, QuestHistory, Reward, Equipment } from '@/types'; // 型定義を活用

// APIレスポンスの型定義
interface GameDataResponse {
    users: User[];
    quests: Quest[];
    rewards: Reward[];
    completedQuests: QuestHistory[];
    pendingQuests: QuestHistory[];
    logs: any[];
    equipments: Equipment[];
    ownedEquipments: any[];
}

interface ChronicleResponse {
    stats: any;
    chronicle: any[];
}

export const useGameData = (onLevelUp?: (info: any) => void) => {
    const queryClient = useQueryClient();

    // 1. メインデータの取得 (Server State)
    const { data: gameData, isLoading: isGameDataLoading } = useQuery<GameDataResponse>({
        queryKey: ['gameData'],
        queryFn: () => apiClient.get('/api/quest/data'),
        // 常に最新データを保つため、ウィンドウフォーカス時も再取得（お好みで調整）
        staleTime: 1000 * 30, // 30秒間はキャッシュを維持
    });

    // 2. 年代記データの取得 (サブデータ)
    const { data: chronicleData } = useQuery<ChronicleResponse>({
        queryKey: ['chronicle'],
        queryFn: () => apiClient.get('/api/quest/family/chronicle'),
        staleTime: 1000 * 60 * 5, // 5分キャッシュ
    });

    // --- Actions (Mutations) ---

    // 汎用的な更新成功時の処理
    const handleSuccess = (message?: string) => {
        // データを再取得して画面を最新にする
        queryClient.invalidateQueries({ queryKey: ['gameData'] });
        queryClient.invalidateQueries({ queryKey: ['chronicle'] });
        if (message) alert(message);
    };

    const handleError = (actionName: string, error: any) => {
        console.error(`${actionName} failed:`, error);
        alert(`${actionName}失敗: ${error.message || '不明なエラー'}`);
    };

    // クエスト完了
    const completeQuestMutation = useMutation({
        mutationFn: async ({ user, quest }: { user: User; quest: Quest }) => {
            return apiClient.post('/api/quest/complete', {
                user_id: user.user_id,
                quest_id: quest.id || quest.quest_id,
            });
        },
        onSuccess: (res, variables) => {
            handleSuccess(res.message); // メッセージがあれば表示

            if (res.earnedMedals > 0) {
                alert(`✨ ラッキー！！ ✨\nちいさなメダル を見つけた！`);
            }

            if (res.leveledUp && onLevelUp) {
                onLevelUp({
                    user: variables.user.name,
                    level: res.newLevel,
                    job: variables.user.job_class
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
        onSuccess: () => handleSuccess(), // キャンセルは静かに更新
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
        onSuccess: (res) => {
            handleSuccess();
            // 承認時のレベルアップも考慮するならここで処理
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
        onSuccess: () => handleSuccess(),
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
        onSuccess: (res) => {
            // 購入成功時はここでinvalidateするが、App.jsx側で結果を受け取りたい場合の対応
            queryClient.invalidateQueries({ queryKey: ['gameData'] });
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
        onSuccess: (res, variables) => {
            handleSuccess(`チャキーン！\n${variables.item.name} を手に入れた！`);
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
        onSuccess: () => handleSuccess(),
        onError: (err) => handleError('装備変更', err),
    });

    // --- インターフェースアダプター (旧コードとの互換性維持) ---

    // App.jsx が期待する関数シグネチャに合わせてラッパー関数を作成
    const completeQuest = (user: User, quest: Quest) => {
        // 申請中チェックなどはUI側(QuestList)で行うのが理想だが、念のためここでも
        const qId = quest.id || quest.quest_id;
        const isPending = gameData?.pendingQuests.some(pq => pq.user_id === user.user_id && pq.quest_id === qId);
        if (isPending) {
            alert("親の承認待ちです。承認されるまでお待ちください！");
            return;
        }
        completeQuestMutation.mutate({ user, quest });
    };

    const cancelQuest = (user: User, historyItem: QuestHistory) => {
        cancelQuestMutation.mutate({ user, history: historyItem });
    };

    const approveQuest = (user: User, historyItem: QuestHistory) => {
        if (!['dad', 'mom'].includes(user.user_id)) {
            alert("承認権限がありません！");
            return;
        }
        if (!window.confirm(`${historyItem.quest_title} を承認しますか？`)) return;
        approveQuestMutation.mutate({ user, history: historyItem });
    };

    const rejectQuest = (user: User, historyItem: QuestHistory) => {
        if (!['dad', 'mom'].includes(user.user_id)) {
            alert("権限がありません！");
            return;
        }
        if (!window.confirm(`${historyItem.quest_title} を再チャレンジ（却下）にしますか？\n子供側の完了状態が解除されます。`)) return;
        rejectQuestMutation.mutate({ user, history: historyItem });
    };

    const buyReward = async (user: User, reward: Reward) => {
        const cost = reward.cost_gold || reward.cost;
        if ((user.gold || 0) < cost) {
            alert("ゴールドが足りません！");
            return { success: false };
        }

        try {
            // App.jsxが await buyReward(...) して結果を待つ作りになっているため
            // mutateAsync を使ってPromiseを返す
            const res = await buyRewardMutation.mutateAsync({ user, reward });
            return { success: true, newGold: res.newGold, reward };
        } catch (e) {
            return { success: false };
        }
    };

    const buyEquipment = (user: User, item: Equipment) => {
        if ((user.gold || 0) < item.cost) {
            alert("ゴールドが足りません！");
            return;
        }
        if (!window.confirm(`${item.name} を購入しますか？`)) return;
        buyEquipmentMutation.mutate({ user, item });
    };

    const changeEquipment = (user: User, item: Equipment) => {
        changeEquipmentMutation.mutate({ user, item });
    };

    const refreshData = () => {
        queryClient.invalidateQueries({ queryKey: ['gameData'] });
    };

    // データをロード中のフォールバック
    // APIからデータが来るまでは masterData の初期値を返す
    const safeUsers = gameData?.users || INITIAL_USERS;
    const safeQuests = gameData?.quests || MASTER_QUESTS;
    const safeRewards = gameData?.rewards || MASTER_REWARDS;

    return {
        users: safeUsers,
        quests: safeQuests,
        rewards: safeRewards,
        completedQuests: gameData?.completedQuests || [],
        pendingQuests: gameData?.pendingQuests || [],
        adventureLogs: gameData?.logs || [],
        equipments: gameData?.equipments || [],
        ownedEquipments: gameData?.ownedEquipments || [],

        familyStats: chronicleData?.stats || null,
        chronicle: chronicleData?.chronicle || [],

        isLoading: isGameDataLoading,

        completeQuest,
        approveQuest,
        rejectQuest,
        cancelQuest,
        buyReward,
        buyEquipment,
        changeEquipment,
        refreshData
    };
};