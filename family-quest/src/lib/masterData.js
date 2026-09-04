// family-quest/src/lib/masterData.js
// サーバー接続エラー時のみ使用されるフォールバックデータ

export const INITIAL_USERS = [
    {
        user_id: 'guest',
        name: '接続エラー',
        job_class: '迷子',
        level: 1,
        exp: 0,
        nextLevelExp: 100,
        gold: 0,
        hp: 1,
        maxHp: 1,
        avatar: '🔌',
        inventory: []
    }
];

// #291: バックエンドのAPIレスポンスと同じフィールド名(quest_id/exp_gain/gold_gain/
// quest_type/icon_key、reward_id/cost_gold/icon_key/description)に統一し、
// フロント側で二重のフィールド名フォールバックを持たずに済むようにする。
// #412(F-L10): _isFallback はこれらがAPI経由で完了できない案内専用の疑似クエストで
// あることを示すフロントエンド拡張フラグ(_isInfiniteと同じ位置づけ)。QuestList.tsx側で
// タップ・長押しの両方を無効化し、タップすると404等のエラーモーダルになる不具合を防ぐ。
export const MASTER_QUESTS = [
    { quest_id: 999, title: '⚠️ サーバーに繋がりません', exp_gain: 0, gold_gain: 0, quest_type: 'daily', days: null, icon_key: '🔌', _isFallback: true },
    { quest_id: 998, title: 'パパに知らせてください', exp_gain: 0, gold_gain: 0, quest_type: 'daily', days: null, icon_key: '👨‍🔧', _isFallback: true },
];

export const MASTER_REWARDS = [
    { reward_id: 999, title: 'データ取得失敗', cost_gold: 99999, category: 'special', icon_key: '❌', description: 'サーバーを確認してください' },
];