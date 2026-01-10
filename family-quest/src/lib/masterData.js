// family-quest/src/constants/masterData.js
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

export const MASTER_QUESTS = [
    { id: 999, title: '⚠️ サーバーに繋がりません', exp: 0, gold: 0, type: 'daily', days: null, icon: '🔌' },
    { id: 998, title: 'パパに知らせてください', exp: 0, gold: 0, type: 'daily', days: null, icon: '👨‍🔧' },
];

export const MASTER_REWARDS = [
    { id: 999, title: 'データ取得失敗', cost: 99999, category: 'special', icon: '❌', desc: 'サーバーを確認してください' },
];