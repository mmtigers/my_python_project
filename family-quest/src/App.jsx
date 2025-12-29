import React, { useState, useEffect } from 'react';
import {
  Sword, Shield, Scroll, ShoppingBag,
  Zap, Undo2, Users, Shirt, Crown, BookOpen, Tent
} from 'lucide-react';

import { INITIAL_USERS, MASTER_QUESTS, MASTER_REWARDS } from './constants/masterData';
import LevelUpModal from './components/ui/LevelUpModal';
import Header from './components/layout/Header';

export default function App() {
  const [viewMode, setViewMode] = useState('user');
  const [activeTab, setActiveTab] = useState('quest');
  const [currentUserIdx, setCurrentUserIdx] = useState(0);

  // 初期値を INITIAL_USERS に設定して、ロード失敗時でも画面が出るようにする
  const [users, setUsers] = useState(INITIAL_USERS);
  const [quests, setQuests] = useState(MASTER_QUESTS);
  const [rewards, setRewards] = useState(MASTER_REWARDS);
  const [completedQuests, setCompletedQuests] = useState([]);
  const [adventureLogs, setAdventureLogs] = useState([]);
  const [isLoading, setIsLoading] = useState(true);

  const [levelUpInfo, setLevelUpInfo] = useState(null);

  // 曜日計算 (0=Sun, 1=Mon...)
  const currentDay = new Date().getDay();

  const fetchGameData = async () => {
    try {
      // 変更点A: 開発環境(Vite)と本番環境で接続先を切り替える
      // (Viteのプロキシ設定をしていない場合、これを入れないと404エラーになります)
      const host = import.meta.env.DEV ? 'http://localhost:8000' : '';
      const res = await fetch(`${host}/api/quest/data`);

      if (!res.ok) throw new Error('Network error');
      const data = await res.json();

      // 変更点B: サーバーから取得した最新のマスタデータでStateを上書きする
      // (これにより、quest_data.py の変更が画面に反映されるようになります)
      if (data.users) setUsers(data.users);
      
      // ▼ 追加箇所ここから ▼
      if (data.quests) setQuests(data.quests);   // サーバー定義のクエストで上書き
      if (data.rewards) setRewards(data.rewards); // サーバー定義の報酬アイテムで上書き
      // ▲ 追加箇所ここまで ▲

      if (data.completedQuests) setCompletedQuests(data.completedQuests);
      if (data.logs) setAdventureLogs(data.logs);
      
      setIsLoading(false);
    } catch (error) {
      console.error("Fetch failed", error);
      // エラー時は初期値(masterData.js)が維持されるので、そのまま続行
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchGameData();
  }, []);

  // ユーザーが存在しない場合のガード
  const currentUser = users[currentUserIdx] || INITIAL_USERS[0] || {};

  // インベントリの安全な取得
  const inventory = currentUser.inventory || [];

  const handleUserSwitch = (idx) => {
    setViewMode('user');
    setCurrentUserIdx(idx);
  };

  const handleQuestClick = async (quest) => {
    const completedEntry = completedQuests.find(
      q => q.user_id === currentUser.user_id && q.quest_id === quest.quest_id
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
            quest_id: quest.quest_id || quest.id // idフォールバック
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
    if (currentUser.gold < (reward.cost_gold || reward.cost)) return;
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

  // 表示フィルタリング
  const availableQuests = quests.filter(q => {
    if (!q.days || q.days.length === 0) return true;
    return q.days.includes(currentDay);
  });

  const todayLogs = adventureLogs.slice(0, 3);

  if (isLoading) return <div className="bg-black text-white h-screen flex items-center justify-center font-mono">LOADING QUEST DATA...</div>;

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
                    <span className="text-sm text-cyan-200">{currentUser.job_class} Lv.{currentUser.level}</span>
                  </div>
                  <div className="grid grid-cols-[30px_1fr] items-center text-sm gap-2">
                    <span className="font-bold text-red-300">HP</span>
                    <div className="w-full bg-gray-900 h-3 rounded border border-gray-600 overflow-hidden">
                      <div className="bg-gradient-to-r from-green-500 to-green-400 h-full" style={{ width: '100%' }}></div>
                    </div>
                    <span className="font-bold text-orange-300">EXP</span>
                    <div className="w-full bg-gray-900 h-3 rounded border border-gray-600 overflow-hidden relative">
                      <div className="bg-gradient-to-r from-orange-500 to-yellow-400 h-full transition-all duration-700"
                        style={{ width: `${(currentUser.exp / currentUser.nextLevelExp) * 100}%` }}></div>
                      <div className="absolute inset-0 text-[8px] flex items-center justify-center text-white/80 font-bold">
                        あと {currentUser.nextLevelExp - currentUser.exp}
                      </div>
                    </div>
                    <span className="font-bold text-yellow-300">G</span>
                    <div className="text-right font-bold text-yellow-300">{currentUser.gold} G</div>
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

            <div className="border-2 border-white bg-black/80 rounded min-h-[320px] p-2 flex flex-col gap-4">
              <div className="flex-1">
                {/* クエストリスト */}
                {activeTab === 'quest' && (
                  <div className="space-y-2 animate-in fade-in slide-in-from-bottom-2 duration-300">
                    <div className="text-center border-b border-gray-600 pb-1 mb-2 text-yellow-300 text-sm font-bold">-- 本日の依頼 --</div>
                    {availableQuests.map(q => {
                      const isDone = completedQuests.some(cq =>
                        cq.user_id === currentUser.user_id && cq.quest_id === (q.quest_id || q.id)
                      );
                      const q_exp = q.exp_gain || q.exp;
                      const q_gold = q.gold_gain || q.gold;

                      return (
                        <div
                          key={q.quest_id || q.id}
                          onClick={() => handleQuestClick(q)}
                          className={`
                            border p-2 rounded flex justify-between items-center cursor-pointer select-none transition-all active:scale-[0.98]
                            ${isDone
                              ? 'border-gray-600 bg-gray-900/50'
                              : 'border-white bg-blue-900/80 hover:bg-blue-800 hover:border-yellow-200'}
                          `}
                        >
                          <div className="flex items-center gap-3">
                            <span className={`text-2xl ${isDone ? 'opacity-30 grayscale' : 'drop-shadow-md'}`}>{q.icon || q.icon_key}</span>
                            <div>
                              <div className={`font-bold ${isDone ? 'text-gray-500 line-through decoration-2' : 'text-white'}`}>{q.title}</div>
                              {!isDone && (
                                <div className="flex gap-2 text-xs">
                                  <span className="text-orange-300">{q_exp} Exp</span>
                                  {q_gold > 0 && <span className="text-yellow-300">{q_gold} G</span>}
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

                {/* 装備リスト */}
                {activeTab === 'equip' && (
                  <div className="animate-in fade-in duration-300">
                    <div className="text-center border-b border-gray-600 pb-1 mb-2 text-yellow-300 text-sm font-bold">-- 現在のそうび --</div>
                    <div className="grid grid-cols-2 gap-2">
                      {inventory.length === 0 ? (
                        <div className="col-span-2 text-center py-8 text-gray-500">
                          そうびは まだ ないようだ...
                        </div>
                      ) : (
                        inventory.map((item, i) => (
                          <div key={i} className="border border-white bg-blue-900 p-2 rounded flex flex-col items-center gap-1">
                            <span className="text-3xl">{item.icon}</span>
                            <span className="font-bold text-sm">{item.title}</span>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                )}

                {/* ショップ */}
                {activeTab === 'shop' && (
                  <div className="space-y-2 animate-in fade-in duration-300">
                    <div className="text-center border-b border-gray-600 pb-1 mb-2 text-yellow-300 text-sm font-bold">-- よろず屋 --</div>
                    {rewards.map(r => {
                      const cost = r.cost_gold || r.cost;
                      return (
                        <div key={r.reward_id || r.id} className="border border-white bg-blue-900 p-2 rounded flex justify-between items-center">
                          <div className="flex items-center gap-3">
                            <span className="text-2xl">{r.icon}</span>
                            <div>
                              <div className="font-bold text-sm">{r.title}</div>
                            </div>
                          </div>
                          <button
                            onClick={() => handleBuyReward(r)}
                            disabled={currentUser.gold < cost}
                            className={`
                              px-2 py-1 text-xs font-bold border rounded min-w-[60px]
                              ${currentUser.gold >= cost
                                ? 'bg-red-700 border-white text-white hover:bg-red-600'
                                : 'bg-gray-700 border-gray-500 text-gray-500 cursor-not-allowed'}
                            `}
                          >
                            {cost} G
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* ログエリア */}
              <div className="border-2 border-dashed border-gray-500 bg-black/50 p-2 rounded min-h-[80px] mt-auto">
                <div className="space-y-1 font-mono text-sm">
                  {todayLogs.map((log, i) => (
                    <div key={log.id} className={i === 0 ? "text-white animate-pulse" : "text-gray-400"}>
                      <span className="mr-1">▶</span>{log.text}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </>
        )}

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
                <div key={u.user_id || u.id} className="bg-blue-800 border-2 border-white rounded p-3 shadow-lg">
                  <div className="flex items-start gap-3">
                    <div className="text-4xl bg-blue-900 p-1.5 rounded border border-white shadow-inner">{u.avatar}</div>
                    <div className="flex-1 space-y-1">
                      <div className="flex justify-between items-baseline border-b border-blue-600 pb-1">
                        <span className="text-lg font-bold text-yellow-300">{u.name}</span>
                        <span className="text-sm text-cyan-200">{u.job_class} Lv.{u.level}</span>
                      </div>
                      <div className="grid grid-cols-[30px_1fr] items-center text-xs gap-2">
                        <span className="font-bold text-red-300">HP</span>
                        <div className="w-full bg-gray-900 h-2 rounded border border-gray-600 overflow-hidden">
                          <div className="bg-green-500 h-full" style={{ width: '100%' }}></div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {viewMode === 'familyLog' && (
          <div className="border-4 border-double border-white bg-blue-900 rounded-lg p-4 shadow-xl min-h-[500px] animate-in fade-in zoom-in-95 duration-300">
            <div className="text-center border-b-2 border-white pb-2 mb-4">
              <h2 className="text-xl font-bold text-yellow-300 flex items-center justify-center gap-2">
                <BookOpen size={24} />
                冒険の書 (履歴)
              </h2>
            </div>
            <div className="h-[400px] overflow-y-auto pr-2 custom-scrollbar space-y-3">
              {adventureLogs.map((log) => (
                <div key={log.id} className="border-b border-white/20 pb-2">
                  <div className="text-[10px] text-cyan-300 mb-0.5 font-bold">
                    {log.timestamp}
                  </div>
                  <div className="text-sm text-gray-100 leading-relaxed">
                    {log.text}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

      </div>
    </div>
  );
}