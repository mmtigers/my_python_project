/**
 * Family Quest Domain Types
 */

// 基本的なID型（数値か文字列か揺らぎがあるためUnion型で吸収）
export type ID = string | number;

// ユーザーロール
export type UserRole = 'dad' | 'mom' | 'child' | 'guest';

// ユーザー情報
export interface User {
    user_id: string; // 'dad', 'mom', etc.
    name: string;
    job_class: string;
    level: number;
    exp: number;
    nextLevelExp: number;
    gold: number;
    hp: number;
    maxHp: number;
    avatar: string; // emoji or url
    inventory: any[]; // 具体的なアイテム構造が決まれば詳細化
}

// クエストの種類
export type QuestType = 'daily' | 'infinite' | 'random' | 'limited';

// クエスト情報
export interface Quest {
    id: ID;          // APIによっては quest_id
    quest_id?: ID;   // APIレスポンスとの互換用
    title: string;
    desc?: string;
    type: QuestType;
    quest_type?: QuestType; // API互換用

    // 報酬
    exp_gain: number; // APIによっては exp
    exp?: number;     // 互換用
    gold_gain?: number;
    gold?: number;    // 互換用

    // 条件・制限
    days?: number[] | null; // 0=Sun, 6=Sat
    start_time?: string;    // "HH:MM"
    end_time?: string;      // "HH:MM"
    target?: string;        // 特定ユーザーID限定など

    // 表示
    icon: string;
    icon_key?: string;

    // フロントエンド制御用拡張フラグ（任意）
    _isInfinite?: boolean;
}

// クエスト履歴・承認待ちアイテム
export interface QuestHistory {
    id: ID;        // history_id
    history_id?: ID;
    user_id: string;
    quest_id: ID;
    quest_title: string;
    status: 'approved' | 'pending' | 'rejected';
    created_at: string;
    approved_at?: string;
}

// ごほうびアイテム
export interface Reward {
    id: ID;
    reward_id?: ID;
    title: string;
    cost: number;
    cost_gold?: number;
    category?: string;
    icon: string;
    icon_key?: string;
    desc?: string;
}

// 装備品
export interface Equipment {
    id: ID;
    equipment_id?: ID;
    name: string;
    cost: number;
    description?: string;
    power?: number;
}