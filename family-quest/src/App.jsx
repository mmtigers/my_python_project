import React, { useState } from 'react';
import {
  Sword, Shield, Scroll, ShoppingBag,
  Zap, Undo2, Users, Shirt, Crown, BookOpen, Tent
} from 'lucide-react';

// --- 新しく作成したファイルのインポート ---
import { INITIAL_USERS, MASTER_QUESTS, MASTER_REWARDS } from './constants/masterData';
import { getDayIndex, getNextLevelExp } from './utils/gameHelpers';
import LevelUpModal from './components/ui/LevelUpModal';
import Header from './components/layout/Header';

export default function App() {
  const [viewMode, setViewMode] = useState('user'); // 'user', 'party', 'familyLog'
  const [activeTab, setActiveTab] = useState('quest');
  const [currentUserIdx, setCurrentUserIdx] = useState(0);
  const [users, setUsers] = useState(INITIAL_USERS);
  const [completedQuests, setCompletedQuests] = useState([]);

  const [adventureLogs, setAdventureLogs] = useState([
    { id: 0, text: 'Family Quest の 世界へ ようこそ！', dateStr: new Date().toISOString().split('T')[0], timestamp: Date.now() }
  ]);

  const [levelUpInfo, setLevelUpInfo] = useState(null);

  const todayStr = new Date().toISOString().split('T')[0];
  const currentDay = getDayIndex();
  const currentUser = users[currentUserIdx];

  // --- ログ追加関数 ---
  const addLog = (text) => {
    const newLog = {
      id: Date.now(),
      text,
      dateStr: new Date().toISOString().split('T')[0],
      timestamp: Date.now()
    };
    setAdventureLogs(prev => [newLog, ...prev]);
  };

  // --- 画面切り替えハンドラ ---
  const handleUserSwitch = (idx) => {
    setViewMode('user');
    setCurrentUserIdx(idx);
    addLog(`${users[idx].name} に プレイヤーを きりかえた！`);
  };

  // --- クエスト処理 ---
  const handleQuestClick = (quest) => {
    const completedEntry = completedQuests.find(q => q.userId === currentUser.id && q.questId === quest.id && q.dateStr === todayStr);
    const updatedUsers = [...users];
    const user = { ...updatedUsers[currentUserIdx] };

    if (completedEntry) {
      if (!window.confirm("この行動を 取り消しますか？")) return;

      user.gold = Math.max(0, user.gold - quest.gold);
      user.exp -= quest.exp;

      while (user.exp < 0 && user.level > 1) {
        user.level -= 1;
        user.nextLevelExp = getNextLevelExp(user.level);
        user.exp += user.nextLevelExp;
      }
      if (user.exp < 0) user.exp = 0;

      setCompletedQuests(prev => prev.filter(q => q !== completedEntry));
      addLog(`${user.name}は ${quest.title} を 取り消した...`);

    } else {
      user.gold += quest.gold;
      user.exp += quest.exp;
      addLog(`${user.name}は ${quest.title} を クリアした！`);
      if (quest.gold > 0) addLog(`${user.name}は ${quest.gold} ゴールド を てにいれた！`);

      while (user.exp >= user.nextLevelExp) {
        user.exp -= user.nextLevelExp;
        user.level += 1;
        user.nextLevelExp = getNextLevelExp(user.level);
        user.hp = user.maxHp;

        setLevelUpInfo({ name: user.name, level: user.level, job: user.job });
        addLog(`ファンファーレ♪ ${user.name}は レベル${user.level} に あがった！`);
      }

      setCompletedQuests([...completedQuests, { userId: user.id, questId: quest.id, dateStr: todayStr }]);
    }
    updatedUsers[currentUserIdx] = user;
    setUsers(updatedUsers);
  };

  const handleBuyReward = (reward) => {
    if (currentUser.gold < reward.cost) {
      addLog(`${currentUser.name}は お金が足りなかった...`);
      return;
    }
    if (!window.confirm(`${reward.title} を 購入しますか？`)) return;

    const updatedUsers = [...users];
    const user = updatedUsers[currentUserIdx];

    user.gold -= reward.cost;

    if (reward.category === 'equip') {
      if (!user.inventory.some(i => i.id === reward.id)) {
        user.inventory.push(reward);
      }
    }

    setUsers(updatedUsers);
    addLog(`${user.name}は ${reward.title} を てにいれた！`);
  };

  const availableQuests = MASTER_QUESTS.filter(q => {
    if (q.days && !q.days.includes(currentDay)) return false;
    return true;
  });

  const todayLogs = adventureLogs.filter(l => l.dateStr === todayStr).slice(0, 3);

  return (
    <div className="min-h-screen bg-black font-mono text-white pb-8 select-none relative overflow-hidden">

      {/* レベルアップモーダル (コンポーネント化済み) */}
      <LevelUpModal info={levelUpInfo} onClose={() => setLevelUpInfo(null)} />

      {/* ヘッダー (コンポーネント化済み) */}
      <Header
        users={users}
        currentUserIdx={currentUserIdx}
        viewMode={viewMode}
        onUserSwitch={handleUserSwitch}
        onPartySwitch={() => setViewMode('party')}
        onLogSwitch={() => setViewMode('familyLog')}
      />

      {/* --- メインコンテンツ --- */}
      <div className="p-4 space-y-4 max-w-md mx-auto">

        {/* ====================
            ユーザーモード (QUEST / EQUIP / SHOP)
           ==================== */}
        {viewMode === 'user' && (
          <>
            {/* ステータスウィンドウ */}
            <div className="border-4 border-double border-white bg-blue-800 rounded-lg p-3 shadow-xl relative animate-in fade-in duration-300">
              <div className="absolute top-2 right-2 opacity-10 pointer-events-none"><Crown size={80} /></div>
              <div className="flex items-start gap-4 relative z-10">
                <div className="text-5xl bg-blue-900 p-2 rounded border-2 border-white shadow-inner">
                  {currentUser.avatar}
                </div>
                <div className="flex-1 space-y-1">
                  <div className="flex justify-between items-baseline border-b border-blue-600 pb-1">
                    <span className="text-lg font-bold text-yellow-300 tracking-widest">{currentUser.name}</span>
                    <span className="text-sm text-cyan-200">{currentUser.job} Lv.{currentUser.level}</span>
                  </div>
                  <div className="grid grid-cols-[30px_1fr] items-center text-sm gap-2">
                    <span className="font-bold text-red-300">HP</span>
                    <div className="w-full bg-gray-900 h-3 rounded border border-gray-600 overflow-hidden">
                      <div className="bg-gradient-to-r from-green-500 to-green-400 h-full" style={{ width: '100%' }}></div>
                    </div>
                    <span className="font-bold text-orange-300">EXP</span>
                    <div className="w-full bg-gray-900 h-3 rounded border border-gray-600 overflow-hidden relative">
                      <div className="bg-gradient-to-r from-orange-500 to-yellow-400 h-full transition-all duration-700" style={{ width: `${(currentUser.exp / currentUser.nextLevelExp) * 100}%` }}></div>
                      <div className="absolute inset-0 text-[8px] flex items-center justify-center text-white/80 font-bold">
                        あと {currentUser.nextLevelExp - currentUser.exp}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* コマンドタブ */}
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

            {/* コンテンツウィンドウ */}
            <div className="border-2 border-white bg-black/80 rounded min-h-[320px] p-2 flex flex-col gap-4">

              <div className="flex-1">
                {/* 1. クエスト */}
                {activeTab === 'quest' && (
                  <div className="space-y-2 animate-in fade-in slide-in-from-bottom-2 duration-300">
                    <div className="text-center border-b border-gray-600 pb-1 mb-2 text-yellow-300 text-sm font-bold">-- 本日の依頼 --</div>
                    {availableQuests.map(q => {
                      const isDone = completedQuests.some(cq => cq.userId === currentUser.id && cq.questId === q.id && cq.dateStr === todayStr);
                      return (
                        <div
                          key={q.id}
                          onClick={() => handleQuestClick(q)}
                          className={`
                            border p-2 rounded flex justify-between items-center cursor-pointer select-none transition-all active:scale-[0.98]
                            ${isDone
                              ? 'border-gray-600 bg-gray-900/50'
                              : 'border-white bg-blue-900/80 hover:bg-blue-800 hover:border-yellow-200'}
                          `}
                        >
                          <div className="flex items-center gap-3">
                            <span className={`text-2xl ${isDone ? 'opacity-30 grayscale' : 'drop-shadow-md'}`}>{q.icon}</span>
                            <div>
                              <div className={`font-bold ${isDone ? 'text-gray-500 line-through decoration-2' : 'text-white'}`}>{q.title}</div>
                              {!isDone && (
                                <div className="flex gap-2 text-xs">
                                  <span className="text-orange-300">{q.exp} Exp</span>
                                  {q.gold > 0 && <span className="text-yellow-300">{q.gold} G</span>}
                                </div>
                              )}
                            </div>
                          </div>
                          {isDone && (
                            <span className="text-red-400 text-xs border border-red-500 px-1 py-0.5 rounded flex items-center gap-1">
                              <Undo2 size={10} /> 戻す
                            </span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}

                {/* 2. そうび */}
                {activeTab === 'equip' && (
                  <div className="animate-in fade-in duration-300">
                    <div className="text-center border-b border-gray-600 pb-1 mb-2 text-yellow-300 text-sm font-bold">-- 現在のそうび --</div>
                    <div className="grid grid-cols-2 gap-2">
                      {currentUser.inventory.filter(i => i.category === 'equip').length === 0 ? (
                        <div className="col-span-2 text-center py-8 text-gray-500">
                          そうびは まだ ないようだ...<br />
                          よろず屋へ 行こう！
                        </div>
                      ) : (
                        currentUser.inventory.filter(i => i.category === 'equip').map((item, i) => (
                          <div key={i} className="border border-white bg-blue-900 p-2 rounded flex flex-col items-center gap-1">
                            <span className="text-3xl">{item.icon}</span>
                            <span className="font-bold text-sm">{item.title}</span>
                            <span className="text-xs text-cyan-200">{item.desc}</span>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                )}

                {/* 3. よろず屋 */}
                {activeTab === 'shop' && (
                  <div className="space-y-2 animate-in fade-in duration-300">
                    <div className="text-center border-b border-gray-600 pb-1 mb-2 text-yellow-300 text-sm font-bold">-- よろず屋 --</div>
                    {MASTER_REWARDS.map(r => (
                      <div key={r.id} className="border border-white bg-blue-900 p-2 rounded flex justify-between items-center">
                        <div className="flex items-center gap-3">
                          <span className="text-2xl">{r.icon}</span>
                          <div>
                            <div className="font-bold text-sm">{r.title}</div>
                            <div className="text-xs text-gray-300">{r.desc}</div>
                          </div>
                        </div>
                        <button
                          onClick={() => handleBuyReward(r)}
                          disabled={currentUser.gold < r.cost}
                          className={`
                            px-2 py-1 text-xs font-bold border rounded min-w-[60px]
                            ${currentUser.gold >= r.cost
                              ? 'bg-red-700 border-white text-white hover:bg-red-600'
                              : 'bg-gray-700 border-gray-500 text-gray-500 cursor-not-allowed'}
                          `}
                        >
                          {r.cost} G
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* 4. 今日のログ */}
              <div className="border-2 border-dashed border-gray-500 bg-black/50 p-2 rounded min-h-[80px] mt-auto">
                {todayLogs.length === 0 ? (
                  <div className="text-gray-500 text-center text-xs mt-4">今日は まだ 何も起きていない...</div>
                ) : (
                  <div className="space-y-1 font-mono text-sm">
                    {todayLogs.map((log, i) => (
                      <div key={log.id} className={i === 0 ? "text-white animate-pulse" : "text-gray-400"}>
                        <span className="mr-1">▶</span>{log.text}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </>
        )}

        {/* ====================
            パーティーモード (ALL MEMBERS)
           ==================== */}
        {viewMode === 'party' && (
          <div className="border-4 border-double border-white bg-blue-900 rounded-lg p-3 shadow-xl min-h-[500px] animate-in fade-in zoom-in-95 duration-300">
            <div className="text-center border-b-2 border-white pb-2 mb-4">
              <h2 className="text-xl font-bold text-yellow-300 flex items-center justify-center gap-2">
                <Tent size={24} />
                パーティーの状態
              </h2>
            </div>

            <div className="space-y-4">
              {users.map((u) => (
                <div key={u.id} className="bg-blue-800 border-2 border-white rounded p-3 shadow-lg">
                  <div className="flex items-start gap-3">
                    <div className="text-4xl bg-blue-900 p-1.5 rounded border border-white shadow-inner">
                      {u.avatar}
                    </div>
                    <div className="flex-1 space-y-1">
                      <div className="flex justify-between items-baseline border-b border-blue-600 pb-1">
                        <span className="text-lg font-bold text-yellow-300">{u.name}</span>
                        <span className="text-sm text-cyan-200">{u.job} Lv.{u.level}</span>
                      </div>

                      {/* ステータスバー */}
                      <div className="grid grid-cols-[30px_1fr] items-center text-xs gap-2">
                        <span className="font-bold text-red-300">HP</span>
                        <div className="w-full bg-gray-900 h-2 rounded border border-gray-600 overflow-hidden">
                          <div className="bg-green-500 h-full" style={{ width: '100%' }}></div>
                        </div>
                        <span className="font-bold text-orange-300">EXP</span>
                        <div className="w-full bg-gray-900 h-2 rounded border border-gray-600 overflow-hidden relative">
                          <div className="bg-orange-500 h-full" style={{ width: `${(u.exp / u.nextLevelExp) * 100}%` }}></div>
                        </div>
                      </div>

                      {/* 装備品 (簡易表示) */}
                      <div className="mt-2 pt-1 border-t border-blue-700">
                        <div className="text-xs text-gray-400 mb-1">そうび:</div>
                        <div className="flex gap-2 min-h-[24px]">
                          {u.inventory.filter(i => i.category === 'equip').length === 0 ? (
                            <span className="text-xs text-gray-600">なし</span>
                          ) : (
                            u.inventory.filter(i => i.category === 'equip').map((item, i) => (
                              <span key={i} className="text-xl bg-blue-900 rounded border border-blue-600 w-8 h-8 flex items-center justify-center" title={item.title}>
                                {item.icon}
                              </span>
                            ))
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ====================
            みんなの記録モード
           ==================== */}
        {viewMode === 'familyLog' && (
          <div className="border-4 border-double border-white bg-blue-900 rounded-lg p-4 shadow-xl min-h-[500px] animate-in fade-in zoom-in-95 duration-300">
            <div className="text-center border-b-2 border-white pb-2 mb-4">
              <h2 className="text-xl font-bold text-yellow-300 flex items-center justify-center gap-2">
                <BookOpen size={24} />
                冒険の書 (履歴)
              </h2>
            </div>

            <div className="h-[400px] overflow-y-auto pr-2 custom-scrollbar space-y-3">
              {adventureLogs.length === 0 ? (
                <div className="text-center text-gray-400 mt-10">まだ 記録は ないようだ...</div>
              ) : (
                adventureLogs.map((log) => (
                  <div key={log.id} className="border-b border-white/20 pb-2">
                    <div className="text-[10px] text-cyan-300 mb-0.5 font-bold">
                      {log.dateStr.replace(/-/g, '/')} {new Date(log.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </div>
                    <div className="text-sm text-gray-100 leading-relaxed">
                      {log.text}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        )}

      </div>
    </div>
  );
}