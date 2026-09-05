// family-quest/src/types/index.ts

// 共通の型定義

// ID型
// #412(品質): 以前は number | string だったが、quest_id/reward_id/history_id等の
// 実カラムはすべてSQLiteのINTEGER PRIMARY KEYであり、サーバーは常にintを返すため
// number のみに絞った(tsc -bが通ることを確認済み)。gameDataSchema.tsのZod検証層は
// 実際のワイヤーデータに対する独立した防御であるため、そちらの union はそのまま残す。
export type ID = number;

// ユーザー情報
// #390: avatar / job_class / role は quest_users の NULL 可カラムのため null を許容する
// (gameDataSchema.ts と対応)。icon はバックエンドが送出しない幽霊フィールドだったため削除。
export interface User {
    user_id: string;
    name: string;
    level: number;
    exp: number;
    avatar?: string | null;
    medal_count?: number | null;
    job_class?: string | null;
    gold: number;
    role?: string | null;
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
// #390: difficulty はバックエンドが送出しない幽霊フィールドだったため削除。
export interface Quest {
    quest_id?: ID;
    title: string;
    description?: string | null;
    exp_gain?: number;
    gold_gain?: number;
    bonus_gold?: number;
    bonus_exp?: number;
    quest_type?: 'daily' | 'weekly' | 'infinite' | 'challenge' | string;
    _isInfinite?: boolean;
    // #412(F-L10): masterData.js のフォールバック(サーバー接続エラー時の案内)専用の
    // 疑似クエストであることを示すフロントエンド拡張フラグ。バックエンドは送出しない。
    _isFallback?: boolean;
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
// #390: status の 'completed' はサーバーが生成しない値、date は送出されない
// 幽霊フィールドだったため削除。gold_earned / exp_earned は NULL 可カラム。
export interface QuestHistory {
    id?: ID;
    user_id: string;
    quest_id: ID;
    quest_title?: string | null;
    status: 'pending' | 'approved' | 'rejected';
    gold_earned?: number | null;
    exp_earned?: number | null;
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

// インベントリアイテム (models/quest.py の InventoryItem に対応)
// #390: desc はサーバー側 Optional[str] のため null を許容する。
export interface InventoryItem {
    id: number;
    reward_id: number;
    title: string;
    icon: string;
    desc?: string | null;
    status: 'owned' | 'consumed';
    purchased_at: string;
    used_at?: string | null;
    category?: string;
    // YouTube連続使用防止クールダウン(15分)の対象かどうか。判定はバックエンド
    // (config.YOUTUBE_REWARD_IDS)側に一本化し、フロントではこのフラグのみ見る。
    is_youtube_reward: boolean;
}

// GET /api/quest/inventory/{user_id} のレスポンス。
// #(YouTubeクールダウン): 単純な配列から、YouTube系ごほうび券の残りクールダウン
// 秒数を併せて返すオブジェクトに変更した。
export interface InventoryResponse {
    items: InventoryItem[];
    youtube_cooldown_remaining_seconds: number;
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