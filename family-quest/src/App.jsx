// family-quest/src/App.jsx
import React, { useState } from 'react';
import { Sword, Shirt, ShoppingBag } from 'lucide-react';

import { INITIAL_USERS } from './constants/masterData';
import { useGameData } from './hooks/useGameData';

import LevelUpModal from './components/ui/LevelUpModal';
import Header from './components/layout/Header';
import UserStatusCard from './components/quest/UserStatusCard';
import QuestList from './components/quest/QuestList';
import ApprovalList from './components/quest/ApprovalList';
import RewardList from './components/quest/RewardList';
import EquipmentShop from './components/quest/EquipmentShop';
import FamilyLog from './components/quest/FamilyLog';
import FamilyParty from './components/quest/FamilyParty';
import AvatarUploader from './components/ui/AvatarUploader';

export default function App() {
  const [viewMode, setViewMode] = useState('user');
  const [activeTab, setActiveTab] = useState('quest');
  const [currentUserIdx, setCurrentUserIdx] = useState(0);
  const [levelUpInfo, setLevelUpInfo] = useState(null);
  // ★追加: アバターアップロード用ステート
  const [editingUser, setEditingUser] = useState(null);

  const {
    users, quests, rewards, completedQuests, pendingQuests, adventureLogs, isLoading,
    equipments, ownedEquipments,
    familyStats, chronicle,
    completeQuest, approveQuest, rejectQuest,
    buyReward, buyEquipment, changeEquipment,
    refreshData // ★追加: 更新後にデータを再取得するために必要 (hooksからexportされているはずです)
  } = useGameData((info) => setLevelUpInfo(info));


  const currentUser = users?.[currentUserIdx] || INITIAL_USERS?.[0] || {};

  // 親かどうか判定
  const isParent = ['dad', 'mom'].includes(currentUser?.user_id);

  const handleUserSwitch = (idx) => {
    setViewMode('user');
    setCurrentUserIdx(idx);
  };

  const handleQuestClick = (quest) => completeQuest(currentUser, quest);
  const handleApprove = (historyItem) => approveQuest(currentUser, historyItem);
  const handleReject = (historyItem) => rejectQuest(currentUser, historyItem); // ★修正: 正しいハンドラ定義
  const handleBuyReward = (reward) => buyReward(currentUser, reward);
  const handleBuyEquipment = (item) => buyEquipment(currentUser, item);
  const handleEquip = (item) => changeEquipment(currentUser, item);

  const todayLogs = adventureLogs ? adventureLogs.slice(0, 3) : [];

  if (isLoading) return <div className="bg-black text-white h-screen flex items-center justify-center font-mono animate-pulse">LOADING ADVENTURE...</div>;

  return (
    <div className="app-container min-h-screen bg-black font-mono text-white pb-8 select-none relative overflow-hidden">
      <LevelUpModal info={levelUpInfo} onClose={() => setLevelUpInfo(null)} />

      {/* ★追加: アバターアップロードモーダル */}
      {editingUser && (
        <AvatarUploader
          user={editingUser}
          onClose={() => setEditingUser(null)}
          onUploadComplete={() => {
            // 画像更新後、データをリフレッシュして画面に即時反映させる
            if (refreshData) refreshData();
          }}
        />
      )}

      <Header
        users={users}
        currentUserIdx={currentUserIdx}
        viewMode={viewMode}
        onUserSwitch={handleUserSwitch}
        onPartySwitch={() => setViewMode('party')}
        onLogSwitch={() => setViewMode('familyLog')}
      />



      <div className="p-4 space-y-4 max-w-md mx-auto">
        {/* 1. ユーザー個別画面 */}
        {viewMode === 'user' && (
          <>
            <UserStatusCard
              user={currentUser}
              onAvatarClick={(user) => setEditingUser(user)}
            />

            {/* ★承認リスト（親のみ表示） */}
            {isParent && pendingQuests.length > 0 && activeTab === 'quest' && (
              <ApprovalList
                pendingQuests={pendingQuests}
                users={users}
                onApprove={handleApprove}
                onReject={handleReject} // ★修正: ここで正しく渡す
              />
            )}

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
                    pendingQuests={pendingQuests}
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

        {/* 2. 記録タブ */}
        {viewMode === 'familyLog' && (
          <FamilyLog stats={familyStats} chronicle={chronicle} />
        )}

        {/* 3. パーティモード */}
        {viewMode === 'party' && (
          <FamilyParty users={users} ownedEquipments={ownedEquipments} />
        )}
      </div>
    </div>
  );
}