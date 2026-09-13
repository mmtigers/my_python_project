// family-quest/src/lib/routineDataSchema.ts
//
// GET /api/routine/today (services/routine_service.py RoutineService.get_today_state)の
// レスポンスに対するランタイム検証。gameDataSchema.ts と同じ方針(未知フィールドは
// 無視するため .strict() は使わない)。
import { z } from 'zod';

const routineStepSchema = z.object({
    key: z.string(),
    label: z.string(),
    icon_key: z.string(),
    is_checkpoint: z.boolean(),
    // 順不同でチェック/チェック解除できる項目(例: 朝の準備5項目)かどうか。
    is_checklist: z.boolean(),
    status: z.enum(['locked', 'current', 'done', 'remind']),
});

const startedFlowSchema = z.object({
    started: z.literal(true),
    title: z.string(),
    checkpoint_time: z.string().nullable(),
    current_step_index: z.number(),
    in_free_time: z.boolean(),
    is_complete: z.boolean(),
    bonus_gold: z.number(),
    bonus_exp: z.number(),
    // チェックリストを1つチェックするたびに増える、出発ボーナスの見込みgold額。
    // チェックポイント通過前はライブプレビュー、通過後はbonus_goldと同じ値になる。
    preview_bonus_gold: z.number(),
    bonus_full_gold: z.number(),
    // チェックポイント通過ボーナスでレベルアップした「その1回のレスポンス」でのみtrue。
    leveled_up: z.boolean(),
    new_level: z.number().nullable(),
    steps: z.array(routineStepSchema),
});

const notStartedFlowSchema = z.object({
    started: z.literal(false),
    title: z.string(),
});

const routineFlowSchema = z.union([startedFlowSchema, notStartedFlowSchema]);

// POST /api/routine/complete のレスポンス(RoutineService._serialize_flowの戻り値、
// 常にstarted=trueの形)の検証に使う。useRoutineData.tsのcompleteStepMutationが利用する。
export const routineActiveFlowSchema = startedFlowSchema;

export const routineTodayResponseSchema = z.object({
    date: z.string(),
    flows: z.object({
        am: routineFlowSchema,
        pm: routineFlowSchema,
    }),
});

export type RoutineStep = z.infer<typeof routineStepSchema>;
export type RoutineFlowState = z.infer<typeof routineFlowSchema>;
export type RoutineActiveFlow = Extract<RoutineFlowState, { started: true }>;
export type RoutineTodayResponse = z.infer<typeof routineTodayResponseSchema>;

// すごろくが「画面を占有すべき状態」(誘導中)かどうか。自由時間中・未開始・
// 完了後は通常のクエスト選択画面に譲る。
export const isRoutineFlowBlocking = (flow: RoutineFlowState | undefined): flow is RoutineActiveFlow =>
    !!flow && flow.started && !flow.is_complete && !flow.in_free_time;

export const isRoutineFlowFreeTime = (flow: RoutineFlowState | undefined): flow is RoutineActiveFlow =>
    !!flow && flow.started && flow.in_free_time;

export type RoutineFlowsMap = RoutineTodayResponse['flows'];

export interface SelectedRoutineFlow {
    // 誘導中(すごろくが画面を占有すべき)のフローキー。無ければnull。
    activeKey: 'am' | 'pm' | null;
    // 誘導中のフローが無い場合のみ、自由時間中バナーを出すべきフローキー。
    freeTimeKey: 'am' | 'pm' | null;
}

// App.tsx(縦画面・自分1人分)とFamilyDashboard.tsx(横画面・FamilyPanelごと)の
// どちらも同じ「amをpmより優先する」判定を必要とするため、ここに集約する
// (元は両ファイルに同じ三項演算子の連鎖が重複していた)。
export const selectRoutineFlow = (flows: RoutineFlowsMap | undefined): SelectedRoutineFlow => {
    if (!flows) return { activeKey: null, freeTimeKey: null };

    const activeKey: 'am' | 'pm' | null = isRoutineFlowBlocking(flows.am)
        ? 'am'
        : isRoutineFlowBlocking(flows.pm)
            ? 'pm'
            : null;

    const freeTimeKey: 'am' | 'pm' | null = activeKey
        ? null
        : isRoutineFlowFreeTime(flows.am)
            ? 'am'
            : isRoutineFlowFreeTime(flows.pm)
                ? 'pm'
                : null;

    return { activeKey, freeTimeKey };
};
