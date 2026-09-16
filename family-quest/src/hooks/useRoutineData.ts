// family-quest/src/hooks/useRoutineData.ts
import { useEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/apiClient';
import { routineActiveFlowSchema, routineTodayResponseSchema, RoutineTodayResponse } from '../lib/routineDataSchema';

// チェックポイント(自由時間の終了)はサーバー側で時刻ベースに強制通過させる遅延評価
// (services/routine_service.py の _apply_forced_transition)のため、フロントは
// 短い間隔でポーリングして「時刻になった瞬間」の反映をそう待たせずに拾う。
const POLL_INTERVAL_MS = 1000 * 15;

// レベルアップ通知(useGameData.tsのonLevelUpと同じ形): チェックポイント通過に
// 伴うレベルアップで呼ばれる。本人がステップを完了した「その場」(completeStepMutation)
// と、何も操作せずポーリングだけで検知される受動的な通過(下のuseEffect)の両方が経路になる。
export type RoutineLevelUpInfo = { newLevel: number };

// 大人用フロー(routine_data.py DAD/MOM_ROUTINE_FLOWS)で、デイリークエストから
// すごろくへ寄せたステップを完了したときの即時報酬。クエスト完了時と同じように
// 「いくらもらえたか」をその場で見せるために呼び出し元へ通知する。
export type RoutineStepRewardInfo = { gold: number; exp: number };

export const useRoutineData = (
    userId: string | undefined,
    onLevelUp?: (info: RoutineLevelUpInfo) => void,
    onError?: (detail: string) => void,
    onStepReward?: (info: RoutineStepRewardInfo) => void,
) => {
    const queryClient = useQueryClient();

    const { data, isLoading, error } = useQuery<RoutineTodayResponse>({
        queryKey: ['routineToday', userId],
        enabled: !!userId,
        queryFn: async () => {
            const raw = await apiClient.get<unknown>(`/api/routine/today?user_id=${encodeURIComponent(userId!)}`);
            return routineTodayResponseSchema.parse(raw);
        },
        staleTime: 1000 * 10,
        refetchInterval: POLL_INTERVAL_MS,
    });

    // コードレビューで発覚: チェックポイント通過ボーナスのレベルアップは、本人が
    // ステップを完了した「その場」(completeStepMutationのonSuccess)だけでなく、
    // 何も操作せず自由時間中に締切時刻を過ぎた場合はポーリング(GET /today)側の
    // _apply_forced_transitionで受動的に起こる。useQueryにはonSuccessが無い(v5)ため、
    // dataの変化をここで監視してonLevelUpを発火する。leveled_upはサーバー側で
    // 通過した「その1回のレスポンス」でのみtrueになる(以後は冪等ガードでfalseに戻る)が、
    // React Queryのキャッシュ再利用(構造共有・再マウント)で同じtrueを2度受け取っても
    // 二重にトーストを出さないよう、日付+flow_keyの組で一度announceしたら覚えておく。
    const announcedLevelUpsRef = useRef<Set<string>>(new Set());
    useEffect(() => {
        if (!data || !onLevelUp) return;
        for (const flowKey of ['am', 'pm'] as const) {
            const flow = data.flows[flowKey];
            if (flow.started && flow.leveled_up && flow.new_level != null) {
                const eventKey = `${data.date}:${flowKey}`;
                if (!announcedLevelUpsRef.current.has(eventKey)) {
                    announcedLevelUpsRef.current.add(eventKey);
                    onLevelUp({ newLevel: flow.new_level });
                }
            }
        }
    }, [data, onLevelUp]);

    const completeStepMutation = useMutation({
        mutationFn: async ({ flowKey, stepKey }: { flowKey: 'am' | 'pm'; stepKey: string }) => {
            const raw = await apiClient.post('/api/routine/complete', {
                user_id: userId,
                flow_key: flowKey,
                step_key: stepKey,
            });
            return routineActiveFlowSchema.parse(raw);
        },
        onSuccess: (res) => {
            queryClient.invalidateQueries({ queryKey: ['routineToday', userId] });
            // ステップ個別報酬はquest_users.gold/expを直接動かすため、ステータス
            // カード(useGameData)側のキャッシュも無効化しないと所持ゴールドの表示が
            // 次のリフェッチまで古いままになる。
            if (res.granted_gold || res.granted_exp) {
                queryClient.invalidateQueries({ queryKey: ['gameData'] });
                onStepReward?.({ gold: res.granted_gold, exp: res.granted_exp });
            }
            // #(コードレビューで発覚): 以前はレスポンスを無条件に破棄しており、
            // チェックポイント通過ボーナスでレベルアップしても(サーバー側では
            // quest_users.levelが更新されているのに)LEVEL UP演出が一切出なかった。
            if (res.leveled_up && res.new_level != null && onLevelUp) {
                onLevelUp({ newLevel: res.new_level });
            }
        },
    });

    // コードレビューで発覚: 完了報告失敗時のエラートースト表示ロジックが
    // App.tsx・FamilyDashboard.tsxの両方に一字一句同じ形で重複していた
    // (handleRoutineStepComplete)。onLevelUpと同じ「呼び出し元のコールバックを
    // 受け取る」形にし、ここへ集約する。
    const completeStep = async (flowKey: 'am' | 'pm', stepKey: string) => {
        try {
            await completeStepMutation.mutateAsync({ flowKey, stepKey });
            return { success: true };
        } catch (e) {
            const detail = e instanceof Error ? e.message : String(e);
            onError?.(detail);
            return { success: false, detail };
        }
    };

    return {
        flows: data?.flows,
        date: data?.date,
        isLoading,
        error,
        completeStep,
        isCompleting: completeStepMutation.isPending,
    };
};
