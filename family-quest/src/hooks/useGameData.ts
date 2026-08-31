import { useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/apiClient';
import { INITIAL_USERS, MASTER_QUESTS, MASTER_REWARDS } from '../lib/masterData';
import { User, Quest, QuestHistory, Reward, QuestResult } from '@/types';

// 新規追加: any型を排除するための厳密なインターフェース定義
// (gameData.logsの1件。バックエンドのQuestService._fetch_recent_logsに対応。
// ★バグ修正(Issue #120): 以前はmessage/created_atという実際には存在しない
// フィールド名を宣言しており、_fetch_recent_logsの実際のレスポンス形状
// {id, text, dateStr, timestamp}と不一致だった。adventureLogsはどの
// コンポーネントからも消費されていないため現状は実害がなかったが、
// 将来利用する際に誤った型に気づかず参照してしまう不具合の種だった)
interface AdventureLog {
    id: string;
    text: string;
    dateStr: string;
    timestamp: string;
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
    userId?: string;
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

export const useGameData = (currentUserIdx: number, onLevelUp?: (info: LevelUpInfo) => void) => {
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

    // 共有クエスト(target_user='siblings'等)のボーナス計算はサーバー側で
    // 「閲覧中のユーザー」の履歴を代表として使うため、直近の応答から現在の
    // currentUserIdxに対応するuser_idを控えておき、次回フェッチ時に送る。
    // (初回フェッチ時点ではまだユーザー一覧が無いため viewer 無しで取得し、
    // 応答が届き次第このrefを更新する。queryKeyには含めないため、ユーザー切替の
    // 度に即時再フェッチはされないが、既存のポーリングや他の操作による
    // invalidateQueriesで数秒以内に反映される)
    const viewerUserIdRef = useRef<string | undefined>(undefined);

    // 1. メインデータの取得
    const { data: gameData, isLoading: isGameDataLoading } = useQuery<GameDataResponse>({
        queryKey: ['gameData'],
        queryFn: () => {
            const viewerUserId = viewerUserIdRef.current;
            const endpoint = viewerUserId
                ? `/api/quest/data?viewer_user_id=${encodeURIComponent(viewerUserId)}`
                : '/api/quest/data';
            return apiClient.get(endpoint);
        },
        staleTime: 1000 * 30,
        refetchInterval: 1000 * 10, // 10秒に1回のポーリングに制限
    });

    useEffect(() => {
        const viewer = gameData?.users?.[currentUserIdx];
        if (viewer) viewerUserIdRef.current = viewer.user_id;
    }, [gameData, currentUserIdx]);

    // 2. 年代記データの取得
    const { data: chronicleData } = useQuery<ChronicleResponse>({
        queryKey: ['chronicle'],
        queryFn: () => apiClient.get('/api/quest/family/chronicle'),
        staleTime: 1000 * 60 * 5,
    });

    // --- Actions (Mutations) ---


    // クエスト完了
    const completeQuestMutation = useMutation({
        mutationFn: async ({ user, quest }: { user: User; quest: Quest }) => {
            return apiClient.post<QuestResult>('/api/quest/complete', { // 型指定
                user_id: user.user_id,
                // #246: quest.id || quest.quest_id という逆順は、useQuestStatus.ts
                // (getQuestLockStateのqId算出、ソースオブトゥルース)が統一した
                // `quest.quest_id || quest.id` という規約と食い違っていた。バックエンドの
                // quest_masterはquest_id列のみを持ちidフィールドは存在しないため現状の
                // 実害は無いが、規約統一のため揃える。
                quest_id: quest.quest_id || quest.id,
            });
        },
        onSuccess: (res, variables) => {
            queryClient.invalidateQueries({ queryKey: ['gameData'] });
            // ★バグ修正: クエスト完了(承認不要な大人の即時完了、または子どもの承認後)は
            // 冒険の記録(年代記)に載るはずだが、chronicleクエリを無効化していなかったため
            // staleTime(5分)が切れるまで反映されなかった。
            queryClient.invalidateQueries({ queryKey: ['chronicle'] });
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
            // 取消は承認済みの完了もロールバックしうる(quest_historyの行ごと削除される)ため、
            // 既に冒険の記録に載っていた場合に備えてこちらも無効化する
            queryClient.invalidateQueries({ queryKey: ['chronicle'] });
        },
        onError: (err) => handleError('キャンセル', err),
    });

    // 承認
    const approveQuestMutation = useMutation({
        mutationFn: async ({ user, history }: { user: User; history: QuestHistory }) => {
            return apiClient.post<QuestResult>('/api/quest/approve', {
                approver_id: user.user_id,
                history_id: history.id ?? history.history_id,
            });
        },
        onSuccess: (res, variables) => {
            queryClient.invalidateQueries({ queryKey: ['gameData'] });
            // 承認によりクエストが approved になり、冒険の記録に載るようになる
            queryClient.invalidateQueries({ queryKey: ['chronicle'] });
            // ★バグ修正(M-6-1): 承認APIのレスポンスにも leveledUp/newLevel が
            // 含まれるが、以前は破棄しており、子どもの承認経由レベルアップ演出が
            // 一切出なかった。レベルアップしたのは承認した親ではなく、クエストを
            // 完了報告した子ども(history.user_id)なので、その本人の情報で通知する。
            if (res.leveledUp && onLevelUp) {
                const completer = gameData?.users.find(u => u.user_id === variables.history.user_id);
                onLevelUp({
                    user: completer?.name || variables.history.user_id,
                    level: res.newLevel,
                    job: completer?.job_class || '無職',
                });
            }
            // ★バグ修正(Issue #238): 兄妹連携クエストのカスケード承認では、相方
            // (自分でタップしなかった方の子ども)側もgold/exp/level/medalが同時に
            // 付与されるが、以前はAPIレスポンスにその情報が一切含まれておらず、
            // 相方のレベルアップ演出を出す手段が無かった。partnerUserIdで相方を
            // 特定し、本人と同様にonLevelUpを呼ぶ。
            if (res.partnerLeveledUp && res.partnerNewLevel != null && onLevelUp) {
                const partner = gameData?.users.find(u => u.user_id === res.partnerUserId);
                onLevelUp({
                    user: partner?.name || res.partnerUserId || '',
                    level: res.partnerNewLevel,
                    job: partner?.job_class || '無職',
                });
            }
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
            // 購入は reward_history に記録され冒険の記録に載る
            queryClient.invalidateQueries({ queryKey: ['chronicle'] });
        },
        onError: (err) => handleError('購入', err),
    });

    // --- ラッパー関数 (Async/Await対応) ---

    const completeQuest = async (user: User, quest: Quest) => {
        // #246: useQuestStatus.tsのgetQuestLockStateと同じ`quest.quest_id || quest.id`
        // の順序に統一する(以前はquest.id || quest.quest_idという逆順だった)。
        const qId = quest.quest_id || quest.id;
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
            // ★バグ修正(M-6-1): 以前はレスポンスを破棄しており、承認画面側で
            // メダル獲得演出(earnedMedals)を出す手段が無かった。leveledUp通知は
            // approveQuestMutationのonSuccess側で行うため、ここではearnedMedalsのみ返す。
            // ★バグ修正(Issue #238): 兄妹連携クエストのカスケード承認時は相方の
            // earnedMedalsもpartnerEarnedMedalsとして返し、呼び出し元でトースト表示の
            // 合算に使えるようにする。
            const res = await approveQuestMutation.mutateAsync({ user, history: historyItem });
            return {
                success: true,
                earnedMedals: res.earnedMedals,
                leveledUp: res.leveledUp,
                partnerEarnedMedals: res.partnerEarnedMedals ?? 0,
            };
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
        isLoading: isGameDataLoading,

        completeQuest,
        approveQuest,
        rejectQuest,
        cancelQuest,
        buyReward,
        refreshData,
    };
};
