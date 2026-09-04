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
// ★フィールド名の統一(Issue #291): 以前はDBの実カラム名(quest_id/exp_gain/
// gold_gain/icon_key/quest_type/target_user)に加え、バックエンドが一部のみ
// 付与していた別名(id/exp/gold/icon/type/target)も型として許容しており、
// どちらが実際に送られてくるか不明瞭だった(id/exp/gold/descは実際には
// 一度もAPIから送られてこない幽霊フィールドだった)。サーバー側の実カラム名に
// 一本化し、フロントの参照側もフォールバック連鎖を廃止した。
export interface Quest {
    quest_id?: ID;
    title: string;
    description?: string;
    difficulty?: number;
    exp_gain?: number;
    gold_gain?: number;
    bonus_gold?: number;
    bonus_exp?: number;
    quest_type?: 'daily' | 'weekly' | 'infinite' | 'challenge' | string;
    _isInfinite?: boolean;
    icon_key?: string;
    start_time?: string;
    end_time?: string;
    days?: number[] | string | null;
    target_user?: string;
    pre_requisite_quest_id?: number | null;
    // ★共有クエスト判定用 (バックエンドの get_available_quests が付与するフィールド)
    is_shared_completed_by?: string;
    shared_completed_by_name?: string;
    is_shared_pending_by?: string;
    shared_pending_by_name?: string;
}

// クエスト履歴
// ★フィールド名の統一(Issue #291): quest_history.id が実カラムであり、
// history_id はAPIから一度も送られてこない幽霊フィールドだったため削除した。
export interface QuestHistory {
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
// ★フィールド名の統一(Issue #291): id/cost/icon/desc は reward_id/cost_gold/
// icon_key/description の別名としてバックエンドが付与していたものだが、
// 二重化を廃止しDBの実カラム名に一本化した。
export interface Reward {
    reward_id?: ID;
    title: string;
    description?: string;
    category?: string;
    cost_gold: number;
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

// #102/#363: クエスト完了APIが実際に成功した時点で App → QuestList/QuestItem へ
// 「完了音・無限クエストのクールダウンを発火せよ」と通知するためのシグナル。
// nonce は同一クエストの連続完了でも useEffect が再発火するよう毎回変える。
// userId は横画面の4人パネル表示で「誰の完了か」を区別するために必須 (#363):
// これが無いと兄が完了した無限クエストのクールダウンが妹・パパ・ママのパネルにも
// 掛かってしまう(サーバー側のクールダウンは (user, quest) 単位)。
export interface CompletedSignal {
    id: ID;
    userId: string;
    nonce: number;
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