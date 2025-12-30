// family-quest/src/App.jsx
import React, { useState, useEffect, useCallback } from 'react';
import {
  Sword, Shirt, ShoppingBag, Undo2, Crown
} from 'lucide-react';

import { INITIAL_USERS, MASTER_QUESTS, MASTER_REWARDS } from './constants/masterData';
import LevelUpModal from './components/ui/LevelUpModal';
import Header from './components/layout/Header';
import { apiClient } from './utils/apiClient';
import RewardList from './components/quest/RewardList';
import EquipmentShop from './components/quest/EquipmentShop';

// --- Components Extraction (UI Components) ---

/**
 * ユーザーステータスカード (HP, EXP, Goldバー) を描画
 */
const UserStatusCard = ({ user }) => {
  if (!user) return null;

  const expPercentage = ((user.exp || 0) / (user.nextLevelExp || 100)) * 100;
  const expRemaining = (user.nextLevelExp || 100) - (user.exp || 0);

  return (
    <div className="border-4 border-double border-white bg-blue-800 rounded-lg p-3 shadow-xl relative animate-in fade-in duration-300">
      <div className="absolute top-2 right-2 opacity-10 pointer-events-none"><Crown size={80} /></div>
      <div className="flex items-start gap-4 relative z-10">
        <div className="text-5xl bg-blue-900 p-2 rounded border-2 border-white shadow-inner">
          {user.avatar || '🙂'}
        </div>
        <div className="flex-1 space-y-1">
          <div className="flex justify-between items-baseline border-b border-blue-600 pb-1">
            <span className="text-lg font-bold text-yellow-300 tracking-widest">{user.name}</span>
            <span className="text-sm text-cyan-200">{user.job_class} Lv.{user.level}</span>
          </div>
          <div className="grid grid-cols-[30px_1fr] items-center text-sm gap-2">
            <span className="font-bold text-red-300">HP</span>
            <div className="w-full bg-gray-900 h-3 rounded border border-gray-600 overflow-hidden">
              <div className="bg-gradient-to-r from-green-500 to-green-400 h-full" style={{ width: '100%' }}></div>
            </div>
            <span className="font-bold text-orange-300">EXP</span>
            <div className="w-full bg-gray-900 h-3 rounded border border-gray-600 overflow-hidden relative">
              <div className="bg-gradient-to-r from-orange-500 to-yellow-400 h-full transition-all duration-700"
                style={{ width: `${expPercentage}%` }}></div>
              <div className="absolute inset-0 text-[8px] flex items-center justify-center text-white/80 font-bold">
                あと {expRemaining}
              </div>
            </div>
            <span className="font-bold text-yellow-300">G</span>
            <div className="text-right font-bold text-yellow-300">{(user.gold || 0).toLocaleString()} G</div>
          </div>
        </div>
      </div>
    </div>
  );
};

/**
 * クエストリストを描画
 */
const QuestList = ({ quests, completedQuests, currentUser, onQuestClick }) => {
  const currentDay = new Date().getDay();

  // 1. フィルタリング（表示対象の抽出）
  const filteredQuests = quests.filter(q => {
    if (q.target !== 'all' && q.target !== currentUser?.user_id) return false;
    if (q.type === 'daily' && q.days) {
      if (!q.days || (Array.isArray(q.days) && q.days.length === 0)) return true;
      const dayList = Array.isArray(q.days) ? q.days : String(q.days).split(',').map(Number);
      return dayList.includes(currentDay);
    }
    return true;
  });

  // 2. ソート（未完了を上、完了を下に）
  const sortedQuests = [...filteredQuests].sort((a, b) => {
    const aId = a.quest_id || a.id;
    const bId = b.quest_id || b.id;

    const aDone = completedQuests.some(cq =>
      cq.user_id === currentUser?.user_id && cq.quest_id === aId
    );
    const bDone = completedQuests.some(cq =>
      cq.user_id === currentUser?.user_id && cq.quest_id === bId
    );

    if (aDone === bDone) return 0; // 同じ状態なら順序維持
    return aDone ? 1 : -1;        // aが完了済みなら後ろへ、未完了なら前へ
  });

  return (
    <div className="space-y-2 animate-in fade-in slide-in-from-bottom-2 duration-300">
      <div className="text-center border-b border-gray-600 pb-1 mb-2 text-yellow-300 text-sm font-bold">-- 本日の依頼 --</div>
      {sortedQuests.map(q => {
        const isRandom = q.type === 'random';
        const isLimited = q.type === 'limited';
        const isPersonal = q.target !== 'all';
        const qId = q.quest_id || q.id;
        const isDone = completedQuests.some(cq =>
          cq.user_id === currentUser?.user_id && cq.quest_id === qId
        );

        return (
          <div key={qId} onClick={() => onQuestClick(q)}
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
  );
};

// --- Custom Hook (Logic Layer) ---

const useGameData = (onLevelUp) => {
  const [users, setUsers] = useState(INITIAL_USERS || []);
  const [quests, setQuests] = useState(MASTER_QUESTS || []);
  const [rewards, setRewards] = useState(MASTER_REWARDS || []);
  const [completedQuests, setCompletedQuests] = useState([]);
  const [adventureLogs, setAdventureLogs] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  // ▼ 追加: 装備関連のstate
  const [equipments, setEquipments] = useState([]);
  const [ownedEquipments, setOwnedEquipments] = useState([]);

  // データ取得: apiClientを使用
  const fetchGameData = useCallback(async () => {
    try {
      const data = await apiClient.get('/api/quest/data');

      if (data.users) setUsers(data.users);
      if (data.quests) setQuests(data.quests);
      if (data.rewards) setRewards(data.rewards);
      if (data.completedQuests) setCompletedQuests(data.completedQuests);
      if (data.logs) setAdventureLogs(data.logs);
      // ▼ 追加: 装備データの反映
      if (data.equipments) setEquipments(data.equipments);
      if (data.ownedEquipments) setOwnedEquipments(data.ownedEquipments);


      // 初回ロード完了
      setIsLoading(false);
    } catch (error) {
      console.error("Game Data Load Error:", error);
      // エラー時はローディング状態を解除し、キャッシュまたは初期値を維持
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchGameData();
  }, [fetchGameData]);

  // Actions
  const completeQuest = async (currentUser, quest) => {
    const q_id = quest.quest_id || quest.id;
    const completedEntry = completedQuests.find(
      q => q.user_id === currentUser.user_id && q.quest_id === q_id
    );

    if (completedEntry) {
      // キャンセル処理
      if (!window.confirm("この行動を 取り消しますか？")) return;
      try {
        await apiClient.post('/api/quest/quest/cancel', {
          user_id: currentUser.user_id,
          history_id: completedEntry.id
        });
        await fetchGameData();
      } catch (e) {
        alert(`キャンセル失敗: ${e.message}`);
      }
    } else {
      // 完了処理
      try {
        const res = await apiClient.post('/api/quest/complete', {
          user_id: currentUser.user_id,
          quest_id: q_id
        });

        await fetchGameData();

        // レベルアップ判定と通知
        if (res.leveledUp && onLevelUp) {
          onLevelUp({
            user: currentUser.name,
            level: res.newLevel,
            job: currentUser.job_class
          });
        }

      } catch (e) {
        alert(`クエスト完了失敗: ${e.message}`);
      }
    }
  };

  const buyReward = async (currentUser, reward) => {
    const cost = reward.cost_gold || reward.cost;
    if ((currentUser?.gold || 0) < cost) {
      alert("ゴールドが足りません！");
      return;
    }
    if (!window.confirm(`${reward.title} を 購入しますか？`)) return;

    try {
      const res = await apiClient.post('/api/quest/reward/purchase', {
        user_id: currentUser.user_id,
        reward_id: reward.reward_id || reward.id
      });

      await fetchGameData();
      alert(`まいどあり！\n${reward.title} を手に入れた！\n(残金: ${res.newGold} G)`);
    } catch (e) {
      alert(`購入失敗: ${e.message}`);
    }
  };

  // ▼ 追加: 装備購入アクション
  const buyEquipment = async (currentUser, item) => {
    if ((currentUser?.gold || 0) < item.cost) {
      alert("ゴールドが足りません！");
      return;
    }
    if (!window.confirm(`${item.name} を購入しますか？`)) return;

    try {
      await apiClient.post('/api/quest/equip/purchase', {
        user_id: currentUser.user_id,
        equipment_id: item.equipment_id
      });
      await fetchGameData();
      alert(`チャキーン！\n${item.name} を手に入れた！`);
    } catch (e) {
      alert(`購入失敗: ${e.message}`);
    }
  };

  // ▼ 追加: 装備変更アクション
  const changeEquipment = async (currentUser, item) => {
    try {
      await apiClient.post('/api/quest/equip/change', {
        user_id: currentUser.user_id,
        equipment_id: item.equipment_id
      });
      await fetchGameData();
    } catch (e) {
      alert(`装備変更失敗: ${e.message}`);
    }
  };

  return {
    users, quests, rewards, completedQuests, adventureLogs, isLoading,
    equipments, ownedEquipments, // 忘れずエクスポート
    completeQuest, buyReward, buyEquipment, changeEquipment // 忘れずエクスポート
  };
};

// --- Main Component ---

export default function App() {
  const [viewMode, setViewMode] = useState('user');
  const [activeTab, setActiveTab] = useState('quest');
  const [currentUserIdx, setCurrentUserIdx] = useState(0);
  const [levelUpInfo, setLevelUpInfo] = useState(null);

  // Hookの使用（レベルアップ時のコールバックを渡す）
  const {
    users, quests, rewards, completedQuests, adventureLogs, isLoading,
    equipments,        // ★ 追加
    ownedEquipments,   // ★ 追加
    completeQuest,
    buyReward,
    buyEquipment,      // ★ 追加
    changeEquipment    // ★ 追加
  } = useGameData((info) => setLevelUpInfo(info));

  const currentUser = users?.[currentUserIdx] || INITIAL_USERS?.[0] || {};

  const handleUserSwitch = (idx) => {
    setViewMode('user');
    setCurrentUserIdx(idx);
  };

  const handleQuestClick = (quest) => completeQuest(currentUser, quest);
  // eslint-disable-next-line no-unused-vars
  const handleBuyReward = (reward) => buyReward(currentUser, reward);

  // ▼ 追加: ハンドラー
  const handleBuyEquipment = (item) => buyEquipment(currentUser, item);
  const handleEquip = (item) => changeEquipment(currentUser, item);

  // 最近のログ（3件）
  const todayLogs = adventureLogs ? adventureLogs.slice(0, 3) : [];

  if (isLoading) return <div className="bg-black text-white h-screen flex items-center justify-center font-mono animate-pulse">LOADING ADVENTURE...</div>;

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
            <UserStatusCard user={currentUser} />

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
                  <QuestList
                    quests={quests}
                    completedQuests={completedQuests}
                    currentUser={currentUser}
                    onQuestClick={handleQuestClick}
                  />
                )}
                {activeTab === 'shop' && (
                  <RewardList
                    rewards={rewards}
                    currentUser={currentUser}
                    onBuy={handleBuyReward}
                  />
                )}
                {activeTab === 'equip' && (
                  <EquipmentShop
                    equipments={equipments}
                    ownedEquipments={ownedEquipments}
                    currentUser={currentUser}
                    onBuy={handleBuyEquipment}
                    onEquip={handleEquip}
                  />
                )}
              </div>
              <div className="border-2 border-dashed border-gray-500 bg-black/50 p-2 rounded min-h-[80px] mt-auto">
                <div className="space-y-1 font-mono text-sm">
                  {todayLogs.map((log) => (
                    <div key={log.id} className="text-gray-400 text-xs">
                      <span className="mr-1 text-blue-500">▶</span>
                      {log.text}
                    </div>
                  ))}
                  {todayLogs.length === 0 && <div className="text-gray-600 text-center text-xs">まだ記録はありません</div>}
                </div>
              </div>
            </div>
          </>
        )}
        {/* Partyモード等の拡張用プレースホルダー */}
        {viewMode !== 'user' && (
          <div className="text-center py-20 text-gray-500">
            COMING SOON...
          </div>
        )}
      </div>
    </div>
  );
}