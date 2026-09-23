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
    // #327: hp/maxHp(バックエンドが送出していたHP関連フィールド)は、UserStatusCard.tsx
    // に対応する表示UIが既に存在せず(いつ・なぜ表示が無くなったか記録が残っていなかった
    // ため一時「要追加確認」だった)、オーナー判断によりHP表示は廃止で確定したため
    // フィールド自体も削除した。バックエンド(MY_HOME_SYSTEM)側は現在も
    // get_all_view_dataでhp/maxHpを送出し続けているが(#471のcalculate_max_hp等)、
    // フロント側では未使用であるため型定義・Zodスキーマから除いている。
    // #470: get_all_view_dataが付与する次レベルまでの必要経験値。
    // gameDataSchema.ts の userSchema にも対応するフィールドを追加済み。
    nextLevelExp?: number;
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
    // #530: バックエンド(models/quest.py MasterQuest.type)が送出しうる値に揃える。
    // 以前の 'weekly' | 'challenge' はサーバーが一度も送出しない値だった。
    quest_type?: 'daily' | 'special' | 'infinite' | 'limited' | 'random' | string;
    _isInfinite?: boolean;
    // #412(F-L10): masterData.js のフォールバック(サーバー接続エラー時の案内)専用の
    // 疑似クエストであることを示すフロントエンド拡張フラグ。バックエンドは送出しない。
    _isFallback?: boolean;
    icon_key?: string;
    start_time?: string;
    end_time?: string;
    // Issue #474: バックエンド(services/quest_service.py の get_all_view_data)は
    // day_of_week カラム(カンマ区切り文字列)を常に number[] | null へ変換してから
    // 送出しており、実際のAPIレスポンスで days が生の文字列になることはない
    // (文字列形式はサーバー内部の MasterQuest.days でのみ使われ、フロントへは渡らない)。
    // 以前この型は number[] | string | null だったが、対応する実際の入力が
    // 存在しない string 分岐だったため削除した。
    days?: number[] | null;
    target_user?: string;
    pre_requisite_quest_id?: number | null;
    // #530: 以前ここにあった is_shared_completed_by / shared_completed_by_name /
    // is_shared_pending_by / shared_pending_by_name は、バックエンドが #371 以降
    // 送出しない(get_available_quests という関数も存在しない)幽霊フィールドだったため削除。
    // 毎日の必須クエスト(常時表示)かボーナスクエスト(折りたたみ表示)かの区分。
    // 欠けている場合(フォールバック用の疑似クエスト等)は必須側として扱う
    // (QuestList.tsx の `q.required !== false` 判定を参照)。
    required?: boolean;
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

// インベントリアイテム (バックエンドの /api/quest/inventory 応答に対応。#409 で models/quest.py の
// InventoryItem は削除されたため、型の対応先はルーター/サービスの応答そのもの)
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
    // YouTubeの視聴制限(クールダウン・日次上限)の対象かどうか。判定はバックエンド
    // (config.YOUTUBE_REWARD_IDS)側に一本化し、フロントではこのフラグのみ見る。
    is_youtube_reward: boolean;
    // この券1枚で視聴できる分数。YouTube系でない券ではnull。
    // 「今日の残り分数に収まらない券」をタップ前に使えない表示にするために使う。
    youtube_duration_minutes?: number | null;
}

// YouTubeの視聴制限の猶予期間中(実際の制限開始前)に表示する予告情報。
// 施行済み、または対象のごほうび券が無い場合はnull。クールダウンと日次上限で
// それぞれ別の施行日を持つため、同じ形の値が2つ返る。
export interface YoutubeCooldownAnnouncement {
    starts_on: string; // ISO日付(YYYY-MM-DD)
    days_remaining: number;
}

// 日次上限を「使い切った後に追加でプリントをやると延ばせる」仕組みの状態。
// 延長機能が無効(対象クエスト未設定など)のときはnull。
export interface YoutubeExtension {
    // プリント1枚で何分延びるか
    minutes_per_quest: number;
    // 今日すでに延長された回数
    granted_count: number;
    // 1日に延長できる上限回数
    max_per_day: number;
    // いまプリントを1枚やれば延長される状態か(上限を使い切っていて回数も残っている)
    can_extend_now: boolean;
}

// GET /api/quest/inventory/{user_id} のレスポンス。
// #(YouTubeクールダウン): 単純な配列から、YouTube系ごほうび券の残りクールダウン
// 秒数を併せて返すオブジェクトに変更した。
export interface InventoryResponse {
    items: InventoryItem[];
    youtube_cooldown_remaining_seconds: number;
    youtube_cooldown_announcement: YoutubeCooldownAnnouncement | null;
    // 1日に使えるYouTube系ごほうび券の合計分数の上限。上限なし設定のときはnull。
    // 施行前でも「今日はあと何分」を表示して慣れてもらうため、猶予期間中も返る。
    youtube_daily_limit_minutes: number | null;
    // JSTの今日すでに使った合計分数。
    youtube_daily_used_minutes: number;
    youtube_daily_limit_announcement: YoutubeCooldownAnnouncement | null;
    // プリントによる上限延長の状態。無効なときはnull。
    youtube_extension: YoutubeExtension | null;
    // YouTube等の時間消費型ごほうびは自由時間中のみ使える(要件確認済み、2026-09-23)。
    is_in_free_time: boolean;
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