import React, { useState, useEffect } from 'react';
import {
  Sword, Shield, Scroll, ShoppingBag,
  Zap, Undo2, Users, Shirt, Crown, BookOpen, Tent
} from 'lucide-react';

// ★インポート名を masterData.js の定義 (INITIAL_USERS, MASTER_QUESTS, MASTER_REWARDS) に合わせる
import { INITIAL_USERS, MASTER_QUESTS, MASTER_REWARDS } from './constants/masterData';
import LevelUpModal from './components/ui/LevelUpModal';
import Header from './components/layout/Header';

export default function App() {
  const [viewMode, setViewMode] = useState('user');
  const [activeTab, setActiveTab] = useState('quest');
  const [currentUserIdx, setCurrentUserIdx] = useState(0);

  // 初期値をフォールバックデータに設定
  const [users, setUsers] = useState(INITIAL_USERS || []);
  const [quests, setQuests] = useState(MASTER_QUESTS || []);
  const [rewards, setRewards] = useState(MASTER_REWARDS || []);
  const [completedQuests, setCompletedQuests] = useState([]);
  const [adventureLogs, setAdventureLogs] = useState([]);
  const [isLoading, setIsLoading] = useState(true);

  const [levelUpInfo, setLevelUpInfo] = useState(null);

  const currentDay = new Date().getDay();

  const fetchGameData = async () => {
    try {
      const host = import.meta.env.DEV ? 'http://localhost:8000' : '';
      const res = await fetch(`${host}/api/quest/data`);
      if (!res.ok) throw new Error('Network error');
      const data = await res.json();

      if (data.users) setUsers(data.users);
      if (data.quests) setQuests(data.quests);
      if (data.rewards) setRewards(data.rewards);
      if (data.completedQuests) setCompletedQuests(data.completedQuests);
      if (data.logs) setAdventureLogs(data.logs);
      
      setIsLoading(false);
    } catch (error) {
      console.error("Fetch failed", error);
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchGameData();
  }, []);

  // クラッシュ防止のガード ( ?. を使用)
  const currentUser = users?.[currentUserIdx] || INITIAL_USERS?.[0] || {};
  const inventory = currentUser?.inventory || [];

  const handleUserSwitch = (idx) => {
    setViewMode('user');
    setCurrentUserIdx(idx);
  };

  const handleQuestClick = async (quest) => {
    const q_id = quest.quest_id || quest.id;
    const completedEntry = completedQuests.find(
      q => q.user_id === currentUser.user_id && q.quest_id === q_id
    );

    if (completedEntry) {
      if (!window.confirm("この行動を 取り消しますか？")) return;
      try {
        const res = await fetch('/api/quest/quest/cancel', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            user_id: currentUser.user_id,
            history_id: completedEntry.id
          })
        });
        if (!res.ok) throw new Error('Cancel failed');
        await fetchGameData();
      } catch (e) {
        alert("通信エラー: キャンセルできませんでした");
      }
    } else {
      try {
        const res = await fetch('/api/quest/complete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            user_id: currentUser.user_id,
            quest_id: q_id
          })
        });
        if (!res.ok) throw new Error('Complete failed');
        const result = await res.json();

        if (result.leveledUp) {
          setLevelUpInfo({
            name: currentUser.name,
            level: result.newLevel,
            job: currentUser.job_class
          });
        }
        await fetchGameData();
      } catch (e) {
        alert("通信エラー: 完了できませんでした");
      }
    }
  };

  const handleBuyReward = async (reward) => {
    const cost = reward.cost_gold || reward.cost;
    if ((currentUser?.gold || 0) < cost) return;
    if (!window.confirm(`${reward.title} を 購入しますか？`)) return;

    try {
      const res = await fetch('/api/quest/reward/purchase', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: currentUser.user_id,
          reward_id: reward.reward_id || reward.id
        })
      });
      if (!res.ok) throw new Error('Purchase failed');
      await fetchGameData();
      alert(`まいどあり！\n${reward.title} を手に入れた！`);
    } catch (e) {
      alert("通信エラー: 購入できませんでした");
    }
  };

  const availableQuests = quests.filter(q => {
    if (q.target !== 'all' && q.target !== currentUser?.user_id) return false;
    if (q.type === 'daily' && q.days && q.days.length > 0) {
      const dayList = Array.isArray(q.days) ? q.days : q.days.split(',').map(Number);
      return dayList.includes(currentDay);
    }
    return true;
  });

  const todayLogs = adventureLogs.slice(0, 3);

  if (isLoading) return <div className="bg-black text-white h-screen flex items-center justify-center font-mono">LOADING ADVENTURE DATA...</div>;

  return (
    <div className="min-h-screen bg-black font-mono text-white pb-8 select-none relative overflow-hidden">
      <LevelUpModal info={levelUpInfo} onClose={() => setLevelUpInfo(null)} />

      <Header
        users={users}
        currentUserIdx={currentUserIdx}
        viewMode={viewMode}
        onUserSwitch={handleUserSwitch}
        onPartySwitch={() => setViewMode('party')}
        onLogSwitch={() => setViewMode('familyLog')}
      />

      <div className="p-4 space-y-4 max-w-md mx-auto">
        {viewMode === 'user' && (
          <>
            <div className="border-4 border-double border-white bg-blue-800 rounded-lg p-3 shadow-xl relative animate-in fade-in duration-300">
              <div className="absolute top-2 right-2 opacity-10 pointer-events-none"><Crown size={80} /></div>
              <div className="flex items-start gap-4 relative z-10">
                <div className="text-5xl bg-blue-900 p-2 rounded border-2 border-white shadow-inner">
                  {currentUser?.avatar}
                </div>
                <div className="flex-1 space-y-1">
                  <div className="flex justify-between items-baseline border-b border-blue-600 pb-1">
                    <span className="text-lg font-bold text-yellow-300 tracking-widest">{currentUser?.name}</span>
                    <span className="text-sm text-cyan-200">{currentUser?.job_class} Lv.{currentUser?.level}</span>
                  </div>
                  <div className="grid grid-cols-[30px_1fr] items-center text-sm gap-2">
                    <span className="font-bold text-red-300">HP</span>
                    <div className="w-full bg-gray-900 h-3 rounded border border-gray-600 overflow-hidden">
                      <div className="bg-gradient-to-r from-green-500 to-green-400 h-full" style={{ width: '100%' }}></div>
                    </div>
                    <span className="font-bold text-orange-300">EXP</span>
                    <div className="w-full bg-gray-900 h-3 rounded border border-gray-600 overflow-hidden relative">
                      <div className="bg-gradient-to-r from-orange-500 to-yellow-400 h-full transition-all duration-700"
                        style={{ width: `${((currentUser?.exp || 0) / (currentUser?.nextLevelExp || 100)) * 100}%` }}></div>
                      <div className="absolute inset-0 text-[8px] flex items-center justify-center text-white/80 font-bold">
                        あと {(currentUser?.nextLevelExp || 100) - (currentUser?.exp || 0)}
                      </div>
                    </div>
                    <span className="font-bold text-yellow-300">G</span>
                    <div className="text-right font-bold text-yellow-300">{(currentUser?.gold || 0).toLocaleString()} G</div>
                  </div>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-3 gap-1 text-center text-xs font-bold">
              {[
                { id: 'quest', label: 'クエスト', icon: Sword },
                { id: 'equip', label: 'そうび', icon: Shirt },
                { id: 'shop', label: 'よろず屋', icon: ShoppingBag },
              ].map(tab => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`
                    border-2 border-white p-2 rounded flex flex-col items-center gap-1 transition-colors
                    ${activeTab === tab.id ? 'bg-red-700 text-white' : 'bg-blue-900 text-gray-300 hover:bg-blue-800'}
                  `}
                >
                  <tab.icon size={18} />
                  {tab.label}
                </button>
              ))}
            </div>

            <div className="border-2 border-white bg-black/80 rounded min-h-[320px] p-2 flex flex-col gap-4">
              <div className="flex-1">
                {activeTab === 'quest' && (
                  <div className="space-y-2 animate-in fade-in slide-in-from-bottom-2 duration-300">
                    <div className="text-center border-b border-gray-600 pb-1 mb-2 text-yellow-300 text-sm font-bold">-- 本日の依頼 --</div>
                    {availableQuests.map(q => {
                      const isRandom = q.type === 'random';
                      const isLimited = q.type === 'limited';
                      const isPersonal = q.target !== 'all';
                      const isDone = completedQuests.some(cq =>
                        cq.user_id === currentUser?.user_id && cq.quest_id === (q.quest_id || q.id)
                      );
                      return (
                        <div key={q.quest_id || q.id} onClick={() => handleQuestClick(q)}
                          className={`border p-2 rounded flex justify-between items-center cursor-pointer select-none transition-all active:scale-[0.98] ${isDone ? 'border-gray-600 bg-gray-900/50' : 'border-white bg-blue-900/80 hover:bg-blue-800 hover:border-yellow-200'}`}>
                          <div className="flex items-center gap-3">
                            <span className={`text-2xl ${isRandom && !isDone ? 'animate-bounce' : ''} ${isDone ? 'opacity-30 grayscale' : ''}`}>{q.icon || q.icon_key}</span>
                            <div>
                              <div className="flex items-center gap-2">
                                {isLimited && !isDone && <span className="bg-red-600 text-[8px] px-1 rounded">期間限定</span>}
                                {isRandom && !isDone && <span className="bg-purple-600 text-[8px] px-1 rounded animate-pulse">レア出現!</span>}
                                {isPersonal && !isDone && <span className="bg-blue-600 text-[8px] px-1 rounded">勅命</span>}
                                <div className={`font-bold ${isDone ? 'text-gray-500 line-through decoration-2' : 'text-white'}`}>{q.title}</div>
                              </div>
                              {!isDone && (
                                <div className="flex gap-2 text-xs">
                                  <span className="text-orange-300">{q.exp_gain || q.exp} Exp</span>
                                  {(q.gold_gain || q.gold) > 0 && <span className="text-yellow-300">{q.gold_gain || q.gold} G</span>}
                                </div>
                              )}
                            </div>
                          </div>
                          {isDone && <span className="text-red-400 text-xs border border-red-500 px-1 py-0.5 rounded flex items-center gap-1"><Undo2 size={10} /> 戻す</span>}
                        </div>
                      );
                    })}
                  </div>
                )}
                {/* 装備・ショップは中略 (構造維持) */}
              </div>
              <div className="border-2 border-dashed border-gray-500 bg-black/50 p-2 rounded min-h-[80px] mt-auto">
                <div className="space-y-1 font-mono text-sm">
                  {todayLogs.map((log) => (
                    <div key={log.id} className="text-gray-400"><span className="mr-1">▶</span>{log.text}</div>
                  ))}
                </div>
              </div>
            </div>
          </>
        )}
        {/* パーティー・履歴は中略 (構造維持) */}
      </div>
    </div>
  );
}