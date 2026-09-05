import { describe, expect, it } from 'vitest';
import { gameDataResponseSchema } from './gameDataSchema';

// #390: バックエンド GameSystem.get_all_view_data (/api/quest/data) の実レスポンス形状を
// fixture 化し、スキーマがそれを受理することを検証する契約テスト。
// fixture は MY_HOME_SYSTEM/services/quest_service.py の get_all_view_data /
// filter_active_quests / _fetch_recent_logs と migrations/0000_baseline_schema.sql、
// 0001_add_quest_users_role.sql、0003_add_reward_master_description.sql、
// 0004_add_coop_quest_link.sql のカラム定義から組み立てている
// (SELECT * のため DB の全カラムがそのまま載る)。
//
// バックエンドのレスポンス形状を変えたら、このfixtureとスキーマの両方を更新すること。
const realisticResponse = {
    users: [
        {
            // quest_users の全カラム + get_all_view_data が付与する nextLevelExp/maxHp/hp
            user_id: 'dad', name: 'まさひろ', job_class: '会社員', level: 3, exp: 120, gold: 45,
            medal_count: 2, avatar: '/uploads/dad_20260901.png', updated_at: '2026-09-01T08:00:00',
            role: 'role_adult', nextLevelExp: 300, maxHp: 65, hp: 65,
        },
        {
            user_id: 'mom', name: 'はるな', job_class: '専業主婦', level: 2, exp: 40, gold: 10,
            medal_count: 0, avatar: '🪄', updated_at: null,
            role: 'role_adult', nextLevelExp: 200, maxHp: 45, hp: 45,
        },
        {
            // quest_data.py に role 無しで追加したメンバー: role は NULL で届く (Issue #390 の再現条件)
            // job_class / avatar も NULL 可カラム
            user_id: 'baby', name: 'あかちゃん', job_class: null, level: 1, exp: 0, gold: 0,
            medal_count: 0, avatar: null, updated_at: null,
            role: null, nextLevelExp: 100, maxHp: 25, hp: 25,
        },
    ],
    quests: [
        {
            // quest_master の全カラム + filter_active_quests が付与する days + bonus_gold/bonus_exp
            quest_id: 1, title: '食器の片付け', description: null, quest_type: 'infinite',
            exp_gain: 10, gold_gain: 5, icon_key: '🍽️', day_of_week: null, target_user: 'all',
            start_date: null, end_date: null, pre_requisite_quest_id: null, occurrence_chance: 1.0,
            start_time: null, end_time: null, reset_period: 'daily',
            days: null, bonus_gold: 0, bonus_exp: 0,
        },
        {
            quest_id: 2, title: '寝かしつけ', description: 'どちらかが対応', quest_type: 'daily',
            exp_gain: 20, gold_gain: 10, icon_key: '🌙', day_of_week: '0,1,2,3,4', target_user: 'role_adult',
            start_date: null, end_date: null, pre_requisite_quest_id: null, occurrence_chance: 1.0,
            start_time: '19:00', end_time: '22:00', reset_period: 'daily',
            days: [0, 1, 2, 3, 4], bonus_gold: 5, bonus_exp: 5,
            // 共有クエスト(role_ プレフィックス)で誰かが対応済み/申請中のときのみ付与される
            is_shared_pending_by: 'mom', shared_pending_by_name: 'はるな',
        },
        {
            quest_id: 3, title: 'きょうだいでお手伝い', description: null, quest_type: 'daily',
            exp_gain: 30, gold_gain: 50, icon_key: '🤝', day_of_week: null, target_user: 'siblings',
            start_date: null, end_date: null, pre_requisite_quest_id: 1, occurrence_chance: 1.0,
            start_time: null, end_time: null, reset_period: 'weekly',
            days: null, bonus_gold: 0, bonus_exp: 0,
        },
    ],
    rewards: [
        {
            // reward_master の全カラム(desc は get_all_view_data が落とす。description は 0003 で追加)
            reward_id: 1, title: 'アイス', cost_gold: 30, category: 'food', icon_key: '🍦',
            description: 'コンビニのアイス1個', target: 'all',
        },
        {
            reward_id: 2, title: 'ゲーム30分', cost_gold: 50, category: null, icon_key: null,
            description: null, target: 'children',
        },
    ],
    completedQuests: [
        {
            // quest_history の全カラム (linked_history_id は 0004 で追加)
            id: 101, user_id: 'dad', quest_id: 1, quest_title: '食器の片付け', status: 'approved',
            completed_at: '2026-09-04T07:30:00', exp_earned: 10, gold_earned: 5, linked_history_id: null,
        },
        {
            // use_item が記録する行 (quest_id=0, exp/gold は 0)
            id: 102, user_id: 'mom', quest_id: 0, quest_title: 'アイテム使用: アイス', status: 'approved',
            completed_at: '2026-09-04T07:45:00', exp_earned: 0, gold_earned: 0, linked_history_id: null,
        },
    ],
    pendingQuests: [
        {
            id: 103, user_id: 'son', quest_id: 3, quest_title: 'きょうだいでお手伝い', status: 'pending',
            completed_at: '2026-09-04T08:00:00', exp_earned: null, gold_earned: null, linked_history_id: 104,
        },
        {
            id: 104, user_id: 'daughter', quest_id: 3, quest_title: 'きょうだいでお手伝い', status: 'pending',
            completed_at: '2026-09-04T08:00:00', exp_earned: null, gold_earned: null, linked_history_id: 103,
        },
    ],
    logs: [
        // _fetch_recent_logs の形状: id は "<type>_<id>" の文字列
        { id: 'quest_101', text: 'まさひろは 食器の片付け をクリアした！', dateStr: '2026-09-04', timestamp: '2026-09-04T07:30:00' },
        { id: 'reward_7', text: 'はるなは アイス を手に入れた！', dateStr: '2026-09-04', timestamp: '2026-09-04T07:20:00' },
    ],
};

describe('gameDataResponseSchema contract with /api/quest/data (#390)', () => {
    it('accepts a realistic get_all_view_data response including null role/avatar/job_class', () => {
        const result = gameDataResponseSchema.safeParse(realisticResponse);
        expect(result.success, result.success ? '' : JSON.stringify(result.error.issues, null, 2)).toBe(true);
    });

    it('keeps nullable user fields as null (no coercion) and ignores unknown DB columns', () => {
        const parsed = gameDataResponseSchema.parse(realisticResponse);
        const baby = parsed.users.find(u => u.user_id === 'baby');
        expect(baby).toMatchObject({ role: null, avatar: null, job_class: null });
        // strict() ではないため、スキーマに無い列(updated_at 等)があっても失敗しない
        expect(parsed.users).toHaveLength(3);
    });

    it('keeps nextLevelExp after parsing instead of silently dropping it (#470)', () => {
        const parsed = gameDataResponseSchema.parse(realisticResponse);
        const dad = parsed.users.find(u => u.user_id === 'dad');
        expect(dad?.nextLevelExp).toBe(300);
    });

    it('rejects a status value the server never produces', () => {
        const broken = {
            ...realisticResponse,
            completedQuests: [{ ...realisticResponse.completedQuests[0], status: 'completed' }],
        };
        expect(gameDataResponseSchema.safeParse(broken).success).toBe(false);
    });

    it('still rejects a response that is missing a required top-level array', () => {
        const rest: Partial<typeof realisticResponse> = { ...realisticResponse };
        delete rest.pendingQuests;
        expect(gameDataResponseSchema.safeParse(rest).success).toBe(false);
    });
});
