import { useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/apiClient';
import { INITIAL_USERS, MASTER_QUESTS, MASTER_REWARDS } from '../lib/masterData';
import { gameDataResponseSchema } from '../lib/gameDataSchema';
import { describeGameDataError, extractErrorDetail } from '../lib/errorDetail';
import { ID, User, Quest, QuestHistory, Reward, QuestResult } from '@/types';

// #412(API契約): gameData.logs(AdventureLog)・chronicle.stats(FamilyStats)は
// どちらもどのコンポーネントからも参照されていない(grep済み)ため型ごと削除した。
// gameDataResponseSchema.ts側のlogsフィールドも合わせて削除済み。将来これらを
// 使う際は、バックエンドの実レスポンス形状(QuestService._fetch_recent_logs /
// UserService.get_family_chronicleのstats)を確認のうえ型を再定義すること。

// 年代記の1エントリ (GameSystem._fetch_full_adventure_logs のレスポンスに対応。
// #291: date/id/avatar_url/message/quest_title/reward_gold/reward_exp/created_at は
// バックエンドから一度も送られてこない幽霊フィールドだったため削除した。
// FamilyLog.tsx側の「複数の代替フィールド名への防御的フォールバック」もあわせて廃止した。
export interface ChronicleItem {
    type?: string;
    timestamp?: string;
    dateStr?: string;
    userId?: string;
    userName?: string;
    userAvatar?: string;
    title?: string;
    text?: string;
    gold?: number;
    exp?: number;
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
}

interface ChronicleResponse {
    chronicle: ChronicleItem[];
}

// models/quest.py の PurchaseResponse に対応。
// #390: 以前は success: boolean と宣言していたがサーバーは status しか返さない
// 幽霊フィールドだったため、実際の形状に合わせる。
interface PurchaseResponse {
    status: string;
    newGold: number;
}

export const useGameData = (currentUserIdx: number, onLevelUp?: (info: LevelUpInfo) => void) => {
    const queryClient = useQueryClient();

    const handleError = (actionName: string, error: unknown) => {
        console.error(`${actionName} failed:`, error);
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
    // #390: 以前は isError / error を捨てており、取得失敗(ネットワーク・Zod検証失敗)は
    // ブラウザの console でしか分からず、画面は INITIAL_USERS(「接続エラー」)か
    // 最後に成功したデータのまま無言だった。error を呼び出し元(App)へ返してバナー表示する。
    const {
        data: gameData,
        isLoading: isGameDataLoading,
        error: gameDataError,
        refetch: refetchGameData,
    } = useQuery<GameDataResponse>({
        queryKey: ['gameData'],
        queryFn: async () => {
            const viewerUserId = viewerUserIdRef.current;
            const endpoint = viewerUserId
                ? `/api/quest/data?viewer_user_id=${encodeURIComponent(viewerUserId)}`
                : '/api/quest/data';
            const raw = await apiClient.get<unknown>(endpoint);
            // #291: バックエンドのレスポンス形状がここで定義したスキーマ(gameDataSchema.ts)と
            // 食い違っている場合、コンポーネント側で無言でundefinedを参照する幽霊フィールド
            // バグとしてではなく、ここで即座にエラーとして検知させる。
            return gameDataResponseSchema.parse(raw) as GameDataResponse;
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
    // #412(API契約): Quest.quest_id は表示用途(マスタ読み込み前のプレースホルダー等)
    // 向けに型としてはoptionalだが、リクエストボディの quest_id は
    // バックエンドの QuestAction(quest_id: int, ge=1)が必須で、undefinedを渡すと
    // JSON.stringifyでキーごと落ちて422になる。ここではmutationFn自体の引数を
    // questId: ID(必須)として分離し、undefinedがそのまま送信経路に乗らないように
    // コンパイル時に強制する(呼び出し元のcompleteQuestでnullチェック済み)。
    const completeQuestMutation = useMutation({
        mutationFn: async ({ user, questId }: { user: User; questId: ID }) => {
            return apiClient.post<QuestResult>('/api/quest/complete', { // 型指定
                user_id: user.user_id,
                quest_id: questId,
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
    // #412(API契約): QuestHistory.id も同様の理由でリクエスト側はhistoryId: ID(必須)に分離する。
    const cancelQuestMutation = useMutation({
        mutationFn: async ({ user, historyId }: { user: User; historyId: ID }) => {
            return apiClient.post('/api/quest/quest/cancel', {
                user_id: user.user_id,
                history_id: historyId,
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
    // #412(API契約): history はonSuccess側でcompleter(申請者)の表示情報を引くために
    // そのまま保持しつつ、リクエストに使うhistoryIdのみID(必須)として分離する。
    const approveQuestMutation = useMutation({
        mutationFn: async ({ user, historyId }: { user: User; history: QuestHistory; historyId: ID }) => {
            return apiClient.post<QuestResult>('/api/quest/approve', {
                approver_id: user.user_id,
                history_id: historyId,
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
    // #412(API契約): historyIdをID(必須)として分離(cancel/approveと同様)。
    const rejectQuestMutation = useMutation({
        mutationFn: async ({ user, historyId, reason }: { user: User; historyId: ID; reason?: string }) => {
            return apiClient.post('/api/quest/reject', {
                approver_id: user.user_id,
                history_id: historyId,
                reason,
            });
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['gameData'] });
        },
        onError: (err) => handleError('却下', err),
    });

    // 報酬購入
    // #412(API契約): rewardId をID(必須)として分離(reward はonSuccess等での
    // 表示用に引き続き保持)。
    const buyRewardMutation = useMutation({
        mutationFn: async ({ user, rewardId }: { user: User; reward: Reward; rewardId: ID }) => {
            return apiClient.post('/api/quest/reward/purchase', {
                user_id: user.user_id,
                reward_id: rewardId,
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
        // #291: quest.id という幽霊フィールドへのフォールバックを廃止し、
        // useQuestStatus.tsのgetQuestLockStateと同じく実カラムのquest_idのみ参照する。
        const qId = quest.quest_id;
        // #412(API契約): quest_id は本来常に存在するはず(サーバー応答はgameDataSchema.ts側で
        // 必須、masterData.jsのフォールバックも必ず付与)だが、Quest型自体は表示専用途向けに
        // optionalなため、undefinedのままリクエストへ渡してしまう(→422)経路を確実に断つ。
        if (qId == null) {
            return { success: false, reason: 'error', detail: 'クエスト情報が正しく取得できていません(再読み込みしてください)' };
        }
        const isPending = gameData?.pendingQuests.some(pq => pq.user_id === user.user_id && pq.quest_id === qId);

        if (isPending) {
            return { success: false, reason: 'pending' };
        }

        try {
            // QuestResult型として受け取る
            const res = await completeQuestMutation.mutateAsync({ user, questId: qId });
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
        // #412(API契約): history_id も同様に未確定のまま送らせない。
        const hId = historyItem.id;
        if (hId == null) {
            return { success: false, reason: 'error', detail: '履歴情報が正しく取得できていません(再読み込みしてください)' };
        }
        try {
            await cancelQuestMutation.mutateAsync({ user, historyId: hId });
            return { success: true };
        } catch (e) {
            return { success: false, reason: 'error', detail: extractErrorDetail(e) };
        }
    };

    const approveQuest = async (user: User, historyItem: QuestHistory) => {
        if (user.role !== 'role_adult') return { success: false, reason: 'permission' };
        // #412(API契約): history_id も同様に未確定のまま送らせない。
        const hId = historyItem.id;
        if (hId == null) {
            return { success: false, reason: 'error', detail: '履歴情報が正しく取得できていません(再読み込みしてください)' };
        }
        try {
            // ★バグ修正(M-6-1): 以前はレスポンスを破棄しており、承認画面側で
            // メダル獲得演出(earnedMedals)を出す手段が無かった。leveledUp通知は
            // approveQuestMutationのonSuccess側で行うため、ここではearnedMedalsのみ返す。
            // ★バグ修正(Issue #238): 兄妹連携クエストのカスケード承認時は相方の
            // earnedMedalsもpartnerEarnedMedalsとして返し、呼び出し元でトースト表示の
            // 合算に使えるようにする。
            const res = await approveQuestMutation.mutateAsync({ user, history: historyItem, historyId: hId });
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
        // #412(API契約): history_id も同様に未確定のまま送らせない。
        const hId = historyItem.id;
        if (hId == null) {
            return { success: false, reason: 'error', detail: '履歴情報が正しく取得できていません(再読み込みしてください)' };
        }
        try {
            await rejectQuestMutation.mutateAsync({ user, historyId: hId, reason: rejectReason });
            return { success: true };
        } catch (e) {
            return { success: false, reason: 'error', detail: extractErrorDetail(e) };
        }
    };

    // buyReward ラッパー
    const buyReward = async (user: User, reward: Reward) => {
        const cost = reward.cost_gold;
        if ((user.gold || 0) < cost) return { success: false, reason: 'gold' };

        // #412(API契約): reward_id も同様に未確定のまま送らせない。
        const rId = reward.reward_id;
        if (rId == null) {
            return { success: false, reason: 'error', detail: '報酬情報が正しく取得できていません(再読み込みしてください)' };
        }

        try {
            const res = await buyRewardMutation.mutateAsync({ user, reward, rewardId: rId }) as unknown as PurchaseResponse;
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
        chronicle: chronicleData?.chronicle || [],
        isLoading: isGameDataLoading,
        // #390: 直近の /api/quest/data 取得失敗。null なら正常。初回成功後の失敗でも
        // data(最後の成功値)は保持されるため、呼び出し元は「古いデータを表示中」の
        // バナーとして使う。
        gameDataError: gameDataError ? describeGameDataError(gameDataError, 'データの取得に失敗しました') : null,
        refetchGameData: () => { void refetchGameData(); },

        completeQuest,
        approveQuest,
        rejectQuest,
        cancelQuest,
        buyReward,
        refreshData,
    };
};
