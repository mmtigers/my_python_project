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
    role?: string;
    // バックエンド(MY_HOME_SYSTEM)から送られてくるHP。個々のプレイヤーはダメージを
    // 受けない仕様のため hp は常に maxHp と等しいが、maxHp 自体は
    // calculate_max_hp(level) = level * 20 + 5 で計算される値なのでフロント側で
    // 独自に再計算してはいけない（旧実装は誤った式で再計算していた）。
    hp?: number;
    maxHp?: number;
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
    pre_requisite_quest_id?: number | null;
    // ★共有クエスト判定用 (バックエンドの get_available_quests が付与するフィールド)
    is_shared_completed_by?: string;
    shared_completed_by_name?: string;
    is_shared_pending_by?: string;
    shared_pending_by_name?: string;
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
    // 兄妹連携クエストの相方側 quest_history.id。承認/却下がサーバー側で
    // この行にもカスケードされる(services/quest_service.py参照)。
    linked_history_id?: ID | null;
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
    target?: string;
}

// インベントリアイテム
export interface InventoryItem {
    id: number;
    reward_id: number;
    title: string;
    icon: string;
    desc: string;
    status: 'owned' | 'consumed';
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
    // 兄妹連携クエストのカスケード承認時のみ、相方(自分でタップしなかった方の
    // 子ども)のレベルアップ/メダル獲得情報が入る。連携クエストでない場合は無し。
    partnerUserId?: string;
    partnerLeveledUp?: boolean;
    partnerNewLevel?: number;
    partnerEarnedMedals?: number;
}