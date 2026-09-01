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
export const MASTER_QUESTS = [
    { quest_id: 999, title: '⚠️ サーバーに繋がりません', exp_gain: 0, gold_gain: 0, quest_type: 'daily', days: null, icon_key: '🔌' },
    { quest_id: 998, title: 'パパに知らせてください', exp_gain: 0, gold_gain: 0, quest_type: 'daily', days: null, icon_key: '👨‍🔧' },
];

export const MASTER_REWARDS = [
    { reward_id: 999, title: 'データ取得失敗', cost_gold: 99999, category: 'special', icon_key: '❌', description: 'サーバーを確認してください' },
];