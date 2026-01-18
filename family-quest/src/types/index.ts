// 共通の型定義

// ID型 (APIによってはnumberだったりstringだったりするため柔軟に)
export type ID = number | string;

// ユーザー情報
export interface User {
    user_id: string; // "dad", "mom" など
    name: string;
    level: number;
    exp: number;
    // ★追加: 修正漏れしていたプロパティ
    avatar?: string;
    icon?: string;
    medal_count?: number;

    job_class?: string;
    gold: number;
    equipment_id?: ID; // 装備中のアイテムID
}

// クエスト情報
export interface Quest {
    id?: ID;
    quest_id?: ID;
    title: string;
    description?: string;
    desc?: string;

    // ★ ? をつけてオプショナルに変更
    difficulty?: number;
    reward_exp?: number;
    reward_gold?: number;

    exp_gain?: number;
    exp?: number;
    gold?: number;
    gold_gain?: number;

    // ★追加: キャリーオーバーボーナス
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

// クエスト履歴 (完了・申請中)
export interface QuestHistory {
    history_id?: ID; // 履歴の一意なID
    id?: ID;         // idの場合もある
    user_id: string;
    quest_id: ID;
    quest_title?: string;
    status: 'pending' | 'approved' | 'rejected' | 'completed';
    date?: string; // YYYY-MM-DD
}

// 報酬アイテム (ショップ)
export interface Reward {
    id?: ID;
    reward_id?: ID;
    title: string;
    desc?: string;
    category?: string; // food, service, item

    cost: number;      // 統一用
    cost_gold?: number; // API生データ用

    icon?: string;     // 絵文字など
    icon_key?: string; // サーバー側のキー
}

// 装備アイテム
export interface Equipment {
    id?: ID;
    equipment_id?: ID;
    name: string;
    description?: string;
    type: 'weapon' | 'armor' | string; // ★修正: stringも許容
    power: number; // 攻撃力 or 防御力
    cost: number;

    icon?: string; // 絵文字
}

export interface Boss {
    bossId: number;
    bossName: string;
    bossIcon: string;
    maxHp: number;
    currentHp: number;
    hpPercentage: number;
    charge: number; // ゲージ等（将来用）
    desc: string;
    isDefeated: boolean;
    weekStartDate: string;
}

// ★修正: APIレスポンス (CompleteResponse相当) に演出用データが含まれる場合がある
export interface QuestResult {
    status: string;
    leveledUp: boolean;
    newLevel: number;
    earnedGold: number;
    earnedExp: number;
    earnedMedals: number;
    message?: string;
    // 追加
    bossEffect?: {
        damage: number;
        remainingHp: number;
        isDefeated: boolean;
        isNewDefeat: boolean;
    };
}