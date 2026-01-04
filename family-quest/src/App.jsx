import React, { useState, useEffect } from 'react';
import { Sword, Shirt, ShoppingBag, RotateCcw } from 'lucide-react'; // RotateCcw 追加

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

// 確認/取消モーダル用コンポーネント (インライン定義)
const ConfirmModal = ({ mode, target, onConfirm, onCancel }) => {
  if (!target) return null;
  const isCancel = mode === 'cancel';

  return (
    <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4 animate-in fade-in">
      <div className="bg-slate-800 border border-slate-600 rounded-lg p-6 max-w-sm w-full shadow-2xl text-center">
        <h3 className={`text-xl font-bold mb-4 ${isCancel ? 'text-red-400' : 'text-blue-400'}`}>
          {isCancel ? '行動の取り消し' : '確認'}
        </h3>
        <p className="text-white mb-6">
          {isCancel ? (
            <>
              「{target.quest_title || target.title}」<br />
              を取り消しますか？<br />
              <span className="text-xs text-gray-400 mt-2 block">
                (獲得した経験値やゴールドは没収されます)
              </span>
            </>
          ) : (
            <>
              「{target.title}」<br />
              を達成しますか？
            </>
          )}
        </p>
        <div className="flex gap-4 justify-center">
          <button onClick={onCancel} className="flex-1 py-3 bg-gray-600 rounded text-white font-bold">
            やめる
          </button>
          <button onClick={onConfirm} className={`flex-1 py-3 rounded text-white font-bold ${isCancel ? 'bg-red-600' : 'bg-blue-600'}`}>
            {isCancel ? '取り消す' : '達成する'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default function App() {
  const [viewMode, setViewMode] = useState('user');
  const [activeTab, setActiveTab] = useState('quest');
  const [currentUserIdx, setCurrentUserIdx] = useState(0);
  const [levelUpInfo, setLevelUpInfo] = useState(null);
  const [editingUser, setEditingUser] = useState(null);

  // キャンセル/確認モーダル用
  const [modalMode, setModalMode] = useState(null); // 'cancel' or null
  const [targetHistory, setTargetHistory] = useState(null);

  const {
    users, quests, rewards, completedQuests, pendingQuests, adventureLogs, isLoading,
    equipments, ownedEquipments,
    familyStats, chronicle,
    completeQuest, approveQuest, rejectQuest, cancelQuest,
    buyReward, buyEquipment, changeEquipment,
    refreshData
  } = useGameData((info) => setLevelUpInfo(info));

  const currentUser = users?.[currentUserIdx] || INITIAL_USERS?.[0] || {};
  const isParent = ['dad', 'mom'].includes(currentUser?.user_id);

  const handleUserSwitch = (idx) => {
    setViewMode('user');
    setCurrentUserIdx(idx);
  };

  // ★修正: QuestList から渡された _isInfinite を使う
  const handleQuestClick = (quest) => {
    const qId = quest.quest_id || quest.id;

    // QuestListで判定済みのフラグ(_isInfinite)があればそれを優先する
    const isInfinite = quest._isInfinite === true || quest.type === 'infinite' || quest.quest_type === 'infinite';

    const isCompleted = completedQuests.some(cq => cq.user_id === currentUser?.user_id && cq.quest_id === qId);
    const isPending = pendingQuests.some(pq => pq.user_id === currentUser?.user_id && pq.quest_id === qId);

    if (isPending) return; // 申請中は無視

    // 無限クエストなら、完了済みでも「キャンセル」ではなく「実施」に進む
    if (isCompleted && !isInfinite) {
      // 通常クエストで完了済みの場合はキャンセル確認へ
      const historyItem = completedQuests.find(cq => cq.user_id === currentUser?.user_id && cq.quest_id === qId);
      setTargetHistory(historyItem);
      setModalMode('cancel');
      return;
    }

    // 未完了、または無限クエストの場合は即実施
    completeQuest(currentUser, quest);
  };

  const handleModalConfirm = async () => {
    if (modalMode === 'cancel' && targetHistory) {
      await cancelQuest(currentUser, targetHistory);
    }
    setModalMode(null);
    setTargetHistory(null);
  };

  const handleApprove = (historyItem) => approveQuest(currentUser, historyItem);
  const handleReject = (historyItem) => rejectQuest(currentUser, historyItem);
  const handleBuyReward = (reward) => buyReward(currentUser, reward);
  const handleBuyEquipment = (item) => buyEquipment(currentUser, item);
  const handleEquip = (item) => changeEquipment(currentUser, item);

  if (isLoading) return <div className="bg-black text-white h-screen flex items-center justify-center font-mono animate-pulse">LOADING ADVENTURE...</div>;

  return (
    <div className="min-h-screen bg-black font-mono text-white pb-8 select-none relative overflow-hidden">
      <LevelUpModal info={levelUpInfo} onClose={() => setLevelUpInfo(null)} />

      {/* アバター編集モーダル */}
      {editingUser && (
        <AvatarUploader
          user={editingUser}
          onClose={() => setEditingUser(null)}
          onUploadComplete={() => { if (refreshData) refreshData(); }}
        />
      )}

      {/* キャンセル確認モーダル */}
      {modalMode && (
        <ConfirmModal
          mode={modalMode}
          target={targetHistory}
          onConfirm={handleModalConfirm}
          onCancel={() => { setModalMode(null); setTargetHistory(null); }}
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

            {/* 親のみ：承認リスト */}
            {isParent && pendingQuests.length > 0 && activeTab === 'quest' && (
              <ApprovalList
                pendingQuests={pendingQuests}
                users={users}
                onApprove={handleApprove}
                onReject={handleReject}
              />
            )}

            {/* タブ切り替え */}
            <div className="grid grid-cols-3 gap-1 text-center text-xs font-bold">
              <button
                onClick={() => setActiveTab('quest')}
                className={`p-2 rounded ${activeTab === 'quest' ? 'bg-yellow-600 text-black' : 'bg-gray-800 text-gray-400'}`}
              >
                <Sword size={16} className="mx-auto mb-1" />
                クエスト
              </button>
              <button
                onClick={() => setActiveTab('shop')}
                className={`p-2 rounded ${activeTab === 'shop' ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400'}`}
              >
                <ShoppingBag size={16} className="mx-auto mb-1" />
                ごほうび
              </button>
              <button
                onClick={() => setActiveTab('equip')}
                className={`p-2 rounded ${activeTab === 'equip' ? 'bg-green-600 text-white' : 'bg-gray-800 text-gray-400'}`}
              >
                <Shirt size={16} className="mx-auto mb-1" />
                そうび
              </button>
            </div>

            {/* コンテンツエリア */}
            <div className="min-h-[300px]">
              {activeTab === 'quest' && (
                <QuestList
                  quests={quests}
                  completedQuests={completedQuests}
                  pendingQuests={pendingQuests}
                  currentUser={currentUser}
                  onQuestClick={handleQuestClick} // 修正したハンドラを使用
                />
              )}

              {activeTab === 'shop' && (
                <RewardList
                  rewards={rewards}
                  userGold={currentUser.gold}
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