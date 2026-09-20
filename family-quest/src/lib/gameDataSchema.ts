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
//
// #752(AUDIT-023): 上記の「新フィールドが無音で無視される」穴を塞ぐため、
// バックエンド側に GET /api/quest/data の response_model
// (MY_HOME_SYSTEM/models/quest.py の GameDataResponse 配下の View モデル)を
// 追加し、MY_HOME_SYSTEM/tests/test_quest_api_type_contract.py が
// **本ファイルを直接読んで** 次の2点をCIで検証するようにした。
//   1. ここで宣言したフィールドをバックエンドが宣言し続けていること
//   2. 「バックエンドにはあるがここに無い」フィールドが、同テストの
//      _BACKEND_ONLY_FIELDS の許可リストと完全に一致すること
// そのため、ここのスキーマにフィールドを足す/削る変更をしたときは、
// バックエンドの同テストも合わせて更新すること(パーサは
// `const xxxSchema = z.object({ ... })` のトップレベルのキー名だけを見る)。
// なお OpenAPI→TS の生成パイプラインは依然として存在しない(#752 の推奨修正)。
import { z } from 'zod';

// #390: quest_users の avatar / job_class / role は NULL 可のカラム
// (migrations/0000_baseline_schema.sql, 0001_add_quest_users_role.sql)。
// quest_data.py に role 無しでメンバーを追加すると "role": null で届くため、
// .optional() だけでは Zod が拒否して全端末が「サーバーに繋がりません」になる。
const userSchema = z.object({
    user_id: z.string(),
    name: z.string(),
    level: z.number(),
    exp: z.number(),
    avatar: z.string().nullable().optional(),
    medal_count: z.number().nullable().optional(),
    job_class: z.string().nullable().optional(),
    gold: z.number(),
    role: z.string().nullable().optional(),
    // #327: hp/maxHpはバックエンドが送出し続けているが、対応する表示UI
    // (UserStatusCard.tsx)が既に存在せずフロント側では未使用のため、
    // オーナー判断(HP表示は廃止で確定)によりスキーマから除いた。.strict()を
    // 使わないため、未知フィールドとして無視されるだけでparseエラーにはならない。
    // #470: get_all_view_dataが実際に付与しているフィールドだが、.strict()を
    // 使わないためこれまでスキーマに含まれておらず、parse後は無音で消えていた
    // (バックエンドの新フィールド追加を検知できないこの設計の既知の穴の一例)。
    nextLevelExp: z.number().optional(),
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
    // Issue #474: 実際のAPIレスポンスでは常に number[] | null (string分岐は
    // 対応する実データが存在しなかったため削除)
    days: z.union([z.array(z.number()), z.null()]).optional(),
    target_user: z.string().nullable().optional(),
    pre_requisite_quest_id: z.number().nullable().optional(),
    // #530: is_shared_* / shared_*_name はバックエンドが送出しないため削除
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

// #390: quest_history.gold_earned / exp_earned は NULL 可のカラム。
// status はサーバーが生成する 'pending' | 'approved' | 'rejected' のみ
// ('completed' はどこにも生成されない値だったため削除)。
const questHistorySchema = z.object({
    id: z.number().optional(),
    user_id: z.string(),
    quest_id: z.union([z.number(), z.string()]),
    quest_title: z.string().nullable().optional(),
    status: z.enum(['pending', 'approved', 'rejected']),
    gold_earned: z.number().nullable().optional(),
    exp_earned: z.number().nullable().optional(),
    linked_history_id: z.union([z.number(), z.string()]).nullable().optional(),
});

// #412(API契約): logs(AdventureLog)はどのコンポーネントからも参照されておらず、
// useGameData.ts側のGameDataResponse/AdventureLog型も削除したため、ここでも
// 検証対象から外す(冒頭のコメントの通り、未知のフィールドはstripされるだけで
// parse自体は失敗しない)。
export const gameDataResponseSchema = z.object({
    users: z.array(userSchema),
    quests: z.array(questSchema),
    rewards: z.array(rewardSchema),
    completedQuests: z.array(questHistorySchema),
    pendingQuests: z.array(questHistorySchema),
});

// #444: models/quest.py の PurchaseResponse に対応。以前はbuyRewardMutationの
// 戻り値を `as unknown as PurchaseResponse` で無検証キャストしており、gameDataと
// 異なりこの検証層を経由していなかった(バックエンドのレスポンス形状が変わっても
// 実行時まで気づけない盲点)。
export const purchaseResponseSchema = z.object({
    status: z.string(),
    newGold: z.number(),
});

// #659: 検証が gameData / purchase / routine の3つに留まっており、chronicle・inventory・
// cameraSettings は無検証のまま(冒頭に掲げた「幽霊フィールド即検知」が半分しか効いて
// いなかった)。残る取得境界もここで明示する。
const chronicleItemSchema = z.object({
    type: z.string().optional(),
    timestamp: z.string().optional(),
    dateStr: z.string().optional(),
    userId: z.string().optional(),
    userName: z.string().optional(),
    userAvatar: z.string().nullable().optional(),
    title: z.string().optional(),
    text: z.string().optional(),
    gold: z.number().optional(),
    exp: z.number().optional(),
});

export const chronicleResponseSchema = z.object({
    chronicle: z.array(chronicleItemSchema),
});

const youtubeCooldownAnnouncementSchema = z.object({
    starts_on: z.string(),
    days_remaining: z.number(),
});

const inventoryItemSchema = z.object({
    id: z.number(),
    reward_id: z.number(),
    title: z.string(),
    icon: z.string(),
    desc: z.string().nullable().optional(),
    status: z.enum(['owned', 'consumed']),
    purchased_at: z.string(),
    used_at: z.string().nullable().optional(),
    category: z.string().nullable().optional(),
    is_youtube_reward: z.boolean(),
});

export const inventoryResponseSchema = z.object({
    items: z.array(inventoryItemSchema),
    youtube_cooldown_remaining_seconds: z.number(),
    youtube_cooldown_announcement: youtubeCooldownAnnouncementSchema.nullable(),
});

// GET /api/cameras/settings のレスポンス(camera_router.py の CameraSettingsResponse)。
export const cameraSettingsResponseSchema = z.array(
    z.object({
        id: z.string(),
        name: z.string(),
        // 表示順。camera_router.get_camera_settings が config.CAMERAS の並び順から採番する。
        order: z.number(),
        enabled: z.boolean(),
    }),
);
