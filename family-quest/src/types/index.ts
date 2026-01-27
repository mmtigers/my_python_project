// family-quest/src/types/index.ts

// 共通の型定義

// ID型
export type ID = number | string;

// ユーザー情報
export interface User {
    user_id: string;
    name: string;
    level: number;
    exp: number;
    avatar?: string;
    icon?: string;
    medal_count?: number;
    job_class?: string;
    gold: number;
    equipment_id?: ID;
}

// クエスト情報
export interface Quest {
    id?: ID;
    quest_id?: ID;
    title: string;
    description?: string;
    desc?: string;
    difficulty?: number;
    reward_exp?: number;
    reward_gold?: number;
    exp_gain?: number;
    exp?: number;
    gold?: number;
    gold_gain?: number;
    bonus_gold?: number;
    bonus_exp?: number;
    quest_type?: 'daily' | 'weekly' | 'infinite' | 'challenge' | string;
    type?: string;
    _isInfinite?: boolean;
    icon?: string;
    icon_key?: string;
    start_time?: string;
    end_time?: string;
    days?: number[] | string | null;
    target?: string;
}

// クエスト履歴
export interface QuestHistory {
    history_id?: ID;
    id?: ID;
    user_id: string;
    quest_id: ID;
    quest_title?: string;
    status: 'pending' | 'approved' | 'rejected' | 'completed';
    date?: string;
    // ★追加: 型エラー修正
    gold_earned?: number;
    exp_earned?: number;
}

// 報酬アイテム
export interface Reward {
    id?: ID;
    reward_id?: ID;
    title: string;
    desc?: string;
    description?: string;
    category?: string;
    cost: number;
    cost_gold?: number;
    icon?: string;
    icon_key?: string;
}

// 装備アイテム
export interface Equipment {
    id?: ID;
    equipment_id?: ID;
    name: string;
    description?: string;
    type: 'weapon' | 'armor' | string;
    power: number;
    cost: number;
    icon?: string;
}

export interface Boss {
    bossId: number;
    bossName: string;
    bossIcon: string;
    maxHp: number;
    currentHp: number;
    hpPercentage: number;
    charge: number;
    desc: string;
    isDefeated: boolean;
    weekStartDate: string;
}

// インベントリアイテム
export interface InventoryItem {
    id: number;
    reward_id: number;
    title: string;
    icon: string;
    desc: string;
    status: 'owned' | 'pending' | 'consumed';
    purchased_at: string;
    category?: string;
}

// ★追加: クエスト完了結果 (APIレスポンス用)
export interface QuestResult {
    status: string;
    leveledUp: boolean;
    newLevel: number;
    earnedGold: number;
    earnedExp: number;
    earnedMedals: number;
    message?: string;
    bossEffect?: BossEffect;
}

// ★追加: ボスダメージ演出用
export interface BossEffect {
    damage: number;
    remainingHp: number;
    isDefeated: boolean;
    isNewDefeat: boolean;
    isCritical?: boolean;
}

// ギルド依頼
export interface Bounty {
    id: number;
    title: string;
    description?: string;
    reward_gold: number;
    target_type: 'ALL' | 'ADULTS' | 'CHILDREN' | 'USER';
    target_user_id?: string;
    status: 'OPEN' | 'TAKEN' | 'PENDING_APPROVAL' | 'COMPLETED' | 'CANCELED';
    created_by: string;
    assignee_id?: string;
    created_at: string;
    is_mine: boolean;
    is_assigned_to_me: boolean;
    can_accept: boolean;
}

// ★追加: 承認待ちインベントリアイテム用 (ApprovalListで使用)
export interface PendingInventory {
    id: number;
    user_id: string;
    user_name: string;
    title: string;
    icon: string;
    used_at: string;
}