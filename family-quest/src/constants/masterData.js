// RPG初期データ & マスタデータ

export const INITIAL_USERS = [
    {
        id: 'dad',
        name: 'まさひろ',
        job: '勇者',
        level: 1,
        exp: 0,
        nextLevelExp: 100,
        gold: 50,
        hp: 25,
        maxHp: 25,
        avatar: '⚔️',
        inventory: []
    },
    {
        id: 'mom',
        name: 'はるな',
        job: '魔法使い',
        level: 1,
        exp: 0,
        nextLevelExp: 100,
        gold: 150,
        hp: 20,
        maxHp: 20,
        avatar: '🪄',
        inventory: []
    },
];

export const MASTER_QUESTS = [
    { id: 1, title: 'お風呂掃除', exp: 20, gold: 10, type: 'daily', days: null, icon: '💧' },
    { id: 2, title: '食器洗い', exp: 15, gold: 5, type: 'daily', days: null, icon: '🍽️' },
    { id: 3, title: '洗濯干し', exp: 15, gold: 5, type: 'daily', days: null, icon: '👕' },
    { id: 4, title: '燃えるゴミ出し', exp: 30, gold: 15, type: 'weekly', days: [1, 4], icon: '🔥' },
    { id: 5, title: 'プラゴミ出し', exp: 30, gold: 15, type: 'weekly', days: [3], icon: '♻️' },
    { id: 6, title: '週末の買い出し', exp: 50, gold: 30, type: 'weekly', days: [6, 0], icon: '🛒' },
    { id: 7, title: '寝かしつけ', exp: 40, gold: 0, type: 'daily', days: null, icon: '💤' },
    { id: 8, title: '保育園送り', exp: 25, gold: 10, type: 'daily', days: [1, 2, 3, 4, 5], icon: '🚲' },
];

export const MASTER_REWARDS = [
    { id: 101, title: '高級アイス', cost: 100, category: 'food', icon: '🍨', desc: 'HP全回復' },
    { id: 102, title: 'ビール/お酒', cost: 150, category: 'food', icon: '🍺', desc: 'MP回復' },
    { id: 103, title: 'マッサージ券', cost: 500, category: 'service', icon: '💆', desc: '肩こり解消' },
    { id: 201, title: 'はやての靴', cost: 3000, category: 'equip', icon: '👟', desc: 'すばやさ+20' },
    { id: 202, title: '勇者のゲーム', cost: 5000, category: 'equip', icon: '🎮', desc: '娯楽+50' },
    { id: 203, title: '時の砂時計', cost: 1000, category: 'special', icon: '⏳', desc: '自由時間' },
    { id: 204, title: '伝説の包丁', cost: 2500, category: 'equip', icon: '🔪', desc: '料理+30' },
];