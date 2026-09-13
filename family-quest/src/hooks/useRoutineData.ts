// family-quest/src/hooks/useRoutineData.ts
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/apiClient';
import { routineActiveFlowSchema, routineTodayResponseSchema, RoutineTodayResponse } from '../lib/routineDataSchema';

// チェックポイント(自由時間の終了)はサーバー側で時刻ベースに強制通過させる遅延評価
// (services/routine_service.py の _apply_forced_transition)のため、フロントは
// 短い間隔でポーリングして「時刻になった瞬間」の反映をそう待たせずに拾う。
const POLL_INTERVAL_MS = 1000 * 15;

// レベルアップ通知(useGameData.tsのonLevelUpと同じ形): 完了報告がチェックポイントの
// 通過を伴い、かつその場でレベルアップした場合にのみ呼ばれる。
export type RoutineLevelUpInfo = { newLevel: number };

export const useRoutineData = (userId: string | undefined, onLevelUp?: (info: RoutineLevelUpInfo) => void) => {
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
            // #(コードレビューで発覚): 以前はレスポンスを無条件に破棄しており、
            // チェックポイント通過ボーナスでレベルアップしても(サーバー側では
            // quest_users.levelが更新されているのに)LEVEL UP演出が一切出なかった。
            if (res.leveled_up && res.new_level != null && onLevelUp) {
                onLevelUp({ newLevel: res.new_level });
            }
        },
    });

    const completeStep = async (flowKey: 'am' | 'pm', stepKey: string) => {
        try {
            await completeStepMutation.mutateAsync({ flowKey, stepKey });
            return { success: true };
        } catch (e) {
            return { success: false, detail: e instanceof Error ? e.message : String(e) };
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
