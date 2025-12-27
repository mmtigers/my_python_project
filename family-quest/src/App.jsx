import React, { useState, useEffect } from 'react';
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

// --- 設定: アイコン変換マップ ---
// サーバーから送られてくる文字列を実際のアイコンコンポーネントに変換します
const ICON_MAP = {
  Gamepad2: <Gamepad2 size={24} />,
  Utensils: <Utensils size={24} />,
  Shirt: <Shirt size={24} />,
  Smile: <Smile size={24} />,
  Moon: <Moon size={24} />,
  Trash2: <Trash2 size={24} />,
  BedDouble: <BedDouble size={24} />,
};

// --- 固定設定: UIの見た目 (ユーザーの色やアバター) ---
const USERS = [
  { id: 'kid1', name: '智矢', avatar: '👦🏻', color: 'bg-green-500', lightColor: 'bg-green-50', borderColor: 'border-green-200', text: 'text-green-600', button: 'bg-green-100 text-green-700' },
  { id: 'kid2', name: '涼花', avatar: '👧🏻', color: 'bg-yellow-400', lightColor: 'bg-yellow-50', borderColor: 'border-yellow-200', text: 'text-yellow-600', button: 'bg-yellow-100 text-yellow-700' },
  { id: 'mom', name: 'ママ', avatar: '👩🏻', color: 'bg-pink-500', lightColor: 'bg-pink-50', borderColor: 'border-pink-200', text: 'text-pink-600', button: 'bg-pink-100 text-pink-700' },
  { id: 'dad', name: 'パパ', avatar: '👨🏻', color: 'bg-blue-500', lightColor: 'bg-blue-50', borderColor: 'border-blue-200', text: 'text-blue-600', button: 'bg-blue-100 text-blue-700' },
];

// --- 固定設定: ごほうびリスト (一旦固定のままにします) ---
const REWARDS = [
  { id: 1, title: 'YouTube 30分', cost: 100, icon: '📺' },
  { id: 2, title: 'おやつ1つ', cost: 50, icon: '🍪' },
  { id: 3, title: '公園にいく', cost: 200, icon: '🛝' },
  { id: 4, title: 'ゲーム 30分', cost: 150, icon: '🎮' },
  { id: 5, title: 'スペシャルガチャ', cost: 500, icon: '🎁' },
];

export default function App() {
  const [activeTab, setActiveTab] = useState('quests'); // 'quests' or 'rewards'

  // データはサーバーから取るので初期値は空にします
  const [quests, setQuests] = useState([]);

  // ポイントもサーバーから取るので初期値は0にします
  const [userPoints, setUserPoints] = useState({
    dad: 0,
    mom: 0,
    kid1: 0,
    kid2: 0,
  });

  const [animatingId, setAnimatingId] = useState(null);

  // ★ 追加: 起動時にデータを取得
  useEffect(() => {
    fetch('/api/quest/data')
      .then(res => res.json())
      .then(data => {
        // 1. ポイント情報の更新
        const newPoints = {};
        // サーバーのユーザーIDをキーにしてポイントを格納
        Object.keys(data.users).forEach(uid => {
          newPoints[uid] = data.users[uid].points;
        });
        // もしDBにないユーザーがいてもエラーにならないよう、現在のstateとマージ
        setUserPoints(prev => ({ ...prev, ...newPoints }));

        // 2. タスク情報の更新
        // サーバーのアイコン名(文字列)をアイコン部品に置換
        const loadedQuests = data.tasks.map(t => ({
          ...t,
          icon: ICON_MAP[t.icon] || <Star size={24} /> // 未定義なら★を表示
        }));
        setQuests(loadedQuests);
      })
      .catch(err => console.error("データ取得に失敗しました:", err));
  }, []);

  // ★ 修正: タスク完了ハンドラ (サーバー通信を追加)
  const toggleQuest = (questId, points, userId) => {
    const questIndex = quests.findIndex(q => q.id === questId);
    if (questIndex === -1) return;

    const quest = quests[questIndex];
    const isCompleting = !quest.isCompleted;

    // 1. 画面を先に更新 (サクサク動くように見せるため)
    const newQuests = [...quests];
    newQuests[questIndex] = { ...quest, isCompleted: isCompleting };
    setQuests(newQuests);

    // アニメーション発火
    if (isCompleting) {
      triggerAnimation(questId);
    }

    // 2. サーバーに送信
    fetch('/api/quest/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_id: userId,
        task_id: questId,
        points: points,
        completed: isCompleting
      })
    })
      .then(res => res.json())
      .then(data => {
        // サーバーから返ってきた最新の正確なポイントで更新
        setUserPoints(prev => ({ ...prev, [userId]: data.newPoints }));
      })
      .catch(err => {
        console.error("通信エラー:", err);
        // エラー時は画面を元に戻す処理などが必要ですが、簡易版なので省略
      });
  };

  const triggerAnimation = (id) => {
    setAnimatingId(id);
    setTimeout(() => setAnimatingId(null), 800);
  };

  // ★ 修正: ごほうび交換ハンドラ (サーバー通信を追加)
  const redeemReward = (cost, userId, userName, rewardTitle) => {
    if (userPoints[userId] >= cost) {
      if (window.confirm(`${userName}がポイントを使って「${rewardTitle}」と交換しますか？`)) {

        // 画面を更新する前にサーバーにリクエスト
        fetch('/api/quest/redeem', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            user_id: userId,
            cost: cost,
            reward_title: rewardTitle
          })
        })
          .then(res => res.json())
          .then(data => {
            // 成功したらポイントを更新
            setUserPoints(prev => ({ ...prev, [userId]: data.newPoints }));
            alert(`交換しました！${userName}、やったね！🎉`);
          })
          .catch(err => {
            console.error("通信エラー:", err);
            alert("エラーが発生しました。");
          });
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
          // userId でフィルタリング
          const userQuests = quests.filter(q => q.userId === user.id);
          const completedCount = userQuests.filter(q => q.isCompleted).length;
          const progress = userQuests.length > 0 ? (completedCount / userQuests.length) * 100 : 0;
          // ポイントは state から取得
          const currentPoint = userPoints[user.id] || 0;

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
                  <span className="font-black text-slate-700 text-sm">{currentPoint}</span>
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
                            {/* アイコンは既にコンポーネント化されています */}
                            {quest.icon && React.isValidElement(quest.icon)
                              ? React.cloneElement(quest.icon, { size: 18 })
                              : <Star size={18} />
                            }
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
                      const canAfford = currentPoint >= reward.cost;
                      return (
                        <button
                          key={reward.id}
                          // 引数を追加しています
                          onClick={() => redeemReward(reward.cost, user.id, user.name, reward.title)}
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