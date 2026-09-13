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
    steps: z.array(routineStepSchema),
});

const notStartedFlowSchema = z.object({
    started: z.literal(false),
    title: z.string(),
});

const routineFlowSchema = z.union([startedFlowSchema, notStartedFlowSchema]);

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
