// family-quest/src/lib/gameDataSchema.ts
//
// #291: バックエンド(MY_HOME_SYSTEM)のAPIレスポンス形状とフロントエンドの型定義
// (src/types/index.ts)が乖離していても、OpenAPI→TS生成パイプラインが無いため
// ビルド時には検知できなかった(フィールド名二重化のような不整合の温床になっていた)。
// GameSystem.get_all_view_data (/api/quest/data) のレスポンスに限り、useGameData.ts
// の取得境界でZodによるランタイム検証を行い、バックエンドが実際に返している
// フィールド名と型をここに明示する。ここに無いフィールドをコンポーネント側で
// 参照しても、たとえTypeScript上は通っても実行時には常にundefinedになる
// ("幽霊フィールド")ことがすぐ分かるようにするのが目的。
//
// 既知でないフィールドは(zodのデフォルト挙動により)無視して構わない。将来
// バックエンドが新しいフィールドを追加した場合にparseが失敗しないよう、
// 意図的に .strict() は使わない。
import { z } from 'zod';

const userSchema = z.object({
    user_id: z.string(),
    name: z.string(),
    level: z.number(),
    exp: z.number(),
    avatar: z.string().optional(),
    medal_count: z.number().optional(),
    job_class: z.string().optional(),
    gold: z.number(),
    role: z.string().optional(),
    hp: z.number().optional(),
    maxHp: z.number().optional(),
});

const questSchema = z.object({
    quest_id: z.number(),
    title: z.string(),
    description: z.string().nullable().optional(),
    exp_gain: z.number().optional(),
    gold_gain: z.number().optional(),
    bonus_gold: z.number().optional(),
    bonus_exp: z.number().optional(),
    quest_type: z.string().optional(),
    icon_key: z.string().nullable().optional(),
    start_time: z.string().nullable().optional(),
    end_time: z.string().nullable().optional(),
    days: z.union([z.array(z.number()), z.string(), z.null()]).optional(),
    target_user: z.string().nullable().optional(),
    pre_requisite_quest_id: z.number().nullable().optional(),
    is_shared_completed_by: z.string().optional(),
    shared_completed_by_name: z.string().optional(),
    is_shared_pending_by: z.string().optional(),
    shared_pending_by_name: z.string().optional(),
});

const rewardSchema = z.object({
    reward_id: z.number(),
    title: z.string(),
    description: z.string().nullable().optional(),
    category: z.string().nullable().optional(),
    cost_gold: z.number(),
    icon_key: z.string().nullable().optional(),
    target: z.string().nullable().optional(),
});

const questHistorySchema = z.object({
    id: z.number().optional(),
    user_id: z.string(),
    quest_id: z.union([z.number(), z.string()]),
    quest_title: z.string().nullable().optional(),
    status: z.enum(['pending', 'approved', 'rejected', 'completed']),
    gold_earned: z.number().optional(),
    exp_earned: z.number().optional(),
    linked_history_id: z.union([z.number(), z.string()]).nullable().optional(),
});

const adventureLogSchema = z.object({
    id: z.string(),
    text: z.string(),
    dateStr: z.string(),
    timestamp: z.string(),
});

export const gameDataResponseSchema = z.object({
    users: z.array(userSchema),
    quests: z.array(questSchema),
    rewards: z.array(rewardSchema),
    completedQuests: z.array(questHistorySchema),
    pendingQuests: z.array(questHistorySchema),
    logs: z.array(adventureLogSchema),
});
