import React, { useState } from 'react';
import {
  Star,
  Check,
  Gift,
  Trophy,
  Utensils,
  Gamepad2,
  BedDouble,
  Trash2,
  Smile,
  Moon,
  Heart,
  Users,
  Shirt
} from 'lucide-react';

// --- モックデータ ---

const USERS = [
  // 子供を優先して表示
  { id: 'kid1', name: '智矢', avatar: '👦🏻', color: 'bg-green-500', lightColor: 'bg-green-50', borderColor: 'border-green-200', text: 'text-green-600', button: 'bg-green-100 text-green-700' },
  { id: 'kid2', name: '涼花', avatar: '👧🏻', color: 'bg-yellow-400', lightColor: 'bg-yellow-50', borderColor: 'border-yellow-200', text: 'text-yellow-600', button: 'bg-yellow-100 text-yellow-700' },
  { id: 'mom', name: 'ママ', avatar: '👩🏻', color: 'bg-pink-500', lightColor: 'bg-pink-50', borderColor: 'border-pink-200', text: 'text-pink-600', button: 'bg-pink-100 text-pink-700' },
  { id: 'dad', name: 'パパ', avatar: '👨🏻', color: 'bg-blue-500', lightColor: 'bg-blue-50', borderColor: 'border-blue-200', text: 'text-blue-600', button: 'bg-blue-100 text-blue-700' },
];

const INITIAL_QUESTS = [
  // お兄ちゃん (5歳) のタスク
  { id: 1, userId: 'kid1', title: 'おもちゃを片付ける', icon: <Gamepad2 size={24} />, points: 10, isCompleted: false },
  { id: 2, userId: 'kid1', title: '食器を下げる', icon: <Utensils size={24} />, points: 20, isCompleted: false },
  { id: 3, userId: 'kid1', title: 'お着替えする', icon: <Shirt size={24} />, points: 15, isCompleted: false },

  // 妹ちゃん (2歳) のタスク
  { id: 4, userId: 'kid2', title: 'はみがき', icon: <Smile size={24} />, points: 50, isCompleted: false },
  { id: 5, userId: 'kid2', title: 'パジャマきる', icon: <Moon size={24} />, points: 30, isCompleted: false },

  // パパ・ママのタスク
  { id: 6, userId: 'dad', title: 'ゴミ出し', icon: <Trash2 size={24} />, points: 50, isCompleted: false },
  { id: 7, userId: 'mom', title: '寝かしつけ', icon: <BedDouble size={24} />, points: 100, isCompleted: false },
];

const REWARDS = [
  { id: 1, title: 'YouTube 30分', cost: 100, icon: '📺' },
  { id: 2, title: 'おやつ1つ', cost: 50, icon: '🍪' },
  { id: 3, title: '公園にいく', cost: 200, icon: '🛝' },
  { id: 4, title: 'ゲーム 30分', cost: 150, icon: '🎮' },
  { id: 5, title: 'スペシャルガチャ', cost: 500, icon: '🎁' },
];

export default function App() {
  const [activeTab, setActiveTab] = useState('quests'); // 'quests' or 'rewards'
  const [quests, setQuests] = useState(INITIAL_QUESTS);

  // ユーザーごとのポイント管理
  const [userPoints, setUserPoints] = useState({
    dad: 120,
    mom: 350,
    kid1: 80,
    kid2: 40,
  });

  // アニメーション用
  const [animatingId, setAnimatingId] = useState(null);

  // クエスト完了ハンドラ
  const toggleQuest = (questId, points, userId) => {
    const questIndex = quests.findIndex(q => q.id === questId);
    const quest = quests[questIndex];

    const isCompleting = !quest.isCompleted;

    const newQuests = [...quests];
    newQuests[questIndex] = { ...quest, isCompleted: isCompleting };
    setQuests(newQuests);

    setUserPoints(prev => ({
      ...prev,
      [userId]: isCompleting
        ? prev[userId] + points
        : Math.max(0, prev[userId] - points)
    }));

    if (isCompleting) {
      triggerAnimation(questId);
    }
  };

  const triggerAnimation = (id) => {
    setAnimatingId(id);
    setTimeout(() => setAnimatingId(null), 800);
  };

  const redeemReward = (cost, userId, userName) => {
    if (userPoints[userId] >= cost) {
      if (window.confirm(`${userName}がポイントを使って交換しますか？`)) {
        setUserPoints(prev => ({
          ...prev,
          [userId]: prev[userId] - cost
        }));
        alert(`交換しました！${userName}、やったね！🎉`);
      }
    } else {
      alert('ポイントが足りないよ！がんばろう！💪');
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 font-sans text-slate-800 pb-24 relative overflow-hidden">

      {/* 共通ヘッダー */}
      <div className="bg-white shadow-sm sticky top-0 z-20 px-4 py-3 flex justify-between items-center">
        <div className="flex items-center gap-2">
          <div className="bg-yellow-400 p-2 rounded-xl text-white">
            <Users size={20} />
          </div>
          <h1 className="font-black text-xl text-slate-700 tracking-tight">Family Quest</h1>
        </div>

        {/* タブ切り替え */}
        <div className="flex bg-slate-100 rounded-lg p-1">
          <button
            onClick={() => setActiveTab('quests')}
            className={`px-3 py-1.5 rounded-md font-bold text-xs transition-all flex items-center gap-1 ${activeTab === 'quests' ? 'bg-white shadow text-slate-800' : 'text-slate-400'
              }`}
          >
            <Check size={14} strokeWidth={3} />
            やること
          </button>
          <button
            onClick={() => setActiveTab('rewards')}
            className={`px-3 py-1.5 rounded-md font-bold text-xs transition-all flex items-center gap-1 ${activeTab === 'rewards' ? 'bg-white shadow text-slate-800' : 'text-slate-400'
              }`}
          >
            <Gift size={14} strokeWidth={3} />
            ごほうび
          </button>
        </div>
      </div>

      {/* 横スクロールコンテナ */}
      <div className="flex gap-4 overflow-x-auto snap-x snap-mandatory px-4 py-6 no-scrollbar items-start">

        {/* ユーザーごとのセクションを表示 */}
        {USERS.map(user => {
          const userQuests = quests.filter(q => q.userId === user.id);
          const completedCount = userQuests.filter(q => q.isCompleted).length;
          const progress = userQuests.length > 0 ? (completedCount / userQuests.length) * 100 : 0;

          return (
            <div key={user.id} className="min-w-[85vw] max-w-[320px] snap-center shrink-0 bg-white rounded-3xl shadow-lg border border-slate-100 overflow-hidden">

              {/* ユーザーヘッダー */}
              <div className={`px-4 py-3 flex items-center justify-between ${user.lightColor} border-b ${user.borderColor}`}>
                <div className="flex items-center gap-3">
                  <span className="text-3xl bg-white rounded-full w-10 h-10 flex items-center justify-center shadow-sm border-2 border-white">
                    {user.avatar}
                  </span>
                  <div>
                    <h2 className="font-bold text-slate-800 text-sm leading-none mb-1">{user.name}</h2>
                    <div className="flex items-center gap-1">
                      <div className="h-1.5 w-16 bg-white/50 rounded-full overflow-hidden">
                        <div className={`h-full ${user.color}`} style={{ width: `${progress}%` }} />
                      </div>
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-1 bg-white px-2 py-1 rounded-full shadow-sm">
                  <Star className="text-yellow-400 fill-yellow-400" size={14} />
                  <span className="font-black text-slate-700 text-sm">{userPoints[user.id]}</span>
                </div>
              </div>

              {/* メインコンテンツ */}
              <div className="p-3">
                {activeTab === 'quests' ? (
                  /* --- クエスト一覧 --- */
                  <div className="space-y-2">
                    {userQuests.map(quest => (
                      <div
                        key={quest.id}
                        onClick={() => toggleQuest(quest.id, quest.points, user.id)}
                        className={`relative group cursor-pointer border rounded-xl p-3 transition-all duration-200 active:scale-95 flex items-center justify-between ${quest.isCompleted
                            ? 'bg-slate-50 border-slate-100 opacity-60'
                            : 'bg-white border-slate-100 shadow-sm hover:border-slate-300'
                          }`}
                      >
                        <div className="flex items-center gap-3">
                          <div className={`p-2 rounded-full ${quest.isCompleted ? 'bg-slate-200 text-slate-400' : `${user.button}`}`}>
                            {React.cloneElement(quest.icon, { size: 18 })}
                          </div>
                          <div>
                            <h3 className={`font-bold text-sm ${quest.isCompleted ? 'line-through text-slate-400' : 'text-slate-700'}`}>
                              {quest.title}
                            </h3>
                            <div className={`flex items-center text-xs font-bold ${user.text}`}>
                              <Star size={10} className="mr-0.5 fill-current" />
                              +{quest.points}
                            </div>
                          </div>
                        </div>

                        <div className={`w-6 h-6 rounded-full border-2 flex items-center justify-center transition-colors ${quest.isCompleted
                            ? 'bg-green-500 border-green-500'
                            : 'bg-white border-slate-200'
                          }`}>
                          {quest.isCompleted && <Check size={14} className="text-white" strokeWidth={4} />}
                        </div>

                        {/* アニメーション */}
                        {animatingId === quest.id && (
                          <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-10">
                            <div className="animate-bounce-up text-3xl">🌟</div>
                          </div>
                        )}
                      </div>
                    ))}
                    {userQuests.length === 0 && (
                      <div className="text-center py-4 text-slate-300 text-xs font-bold">
                        タスクなし
                      </div>
                    )}
                  </div>
                ) : (
                  /* --- リワード一覧 --- */
                  <div className="grid grid-cols-2 gap-2">
                    {REWARDS.map(reward => {
                      const canAfford = userPoints[user.id] >= reward.cost;
                      return (
                        <button
                          key={reward.id}
                          onClick={() => redeemReward(reward.cost, user.id, user.name)}
                          disabled={!canAfford}
                          className={`p-2 rounded-xl border text-center transition-all ${canAfford
                              ? 'bg-white border-slate-200 shadow hover:shadow-md'
                              : 'bg-slate-50 border-slate-100 opacity-50 cursor-not-allowed'
                            }`}
                        >
                          <div className="text-2xl mb-1">{reward.icon}</div>
                          <div className="font-bold text-slate-600 text-xs mb-1 truncate">{reward.title}</div>
                          <div className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-black ${canAfford ? 'bg-yellow-100 text-yellow-600' : 'bg-slate-200 text-slate-500'
                            }`}>
                            <Star size={8} className="mr-0.5 fill-current" />
                            {reward.cost}
                          </div>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <style>{`
        .no-scrollbar::-webkit-scrollbar {
          display: none;
        }
        .no-scrollbar {
          -ms-overflow-style: none;
          scrollbar-width: none;
        }
        @keyframes bounce-up {
          0% { transform: translateY(0) scale(0.5); opacity: 0; }
          50% { transform: translateY(-20px) scale(1.2); opacity: 1; }
          100% { transform: translateY(-40px) scale(1); opacity: 0; }
        }
        .animate-bounce-up {
          animation: bounce-up 0.8s ease-out forwards;
        }
      `}</style>
    </div>
  );
}
