import React, { useState } from 'react';
import { Sword, Shirt, ShoppingBag } from 'lucide-react';

// ★パス変更: libから読み込み
import { INITIAL_USERS } from './lib/masterData';
import { useGameData } from './hooks/useGameData';

// UIコンポーネント (共通パーツ)
import LevelUpModal from './components/ui/LevelUpModal';
import Header from './components/layout/Header';
import AvatarUploader from './components/ui/AvatarUploader';
import MessageModal from './components/ui/MessageModal';

// ★パス変更: 機能(Feature)フォルダから読み込み
import UserStatusCard from './features/family/components/UserStatusCard';
import QuestList from './features/quest/components/QuestList';
import ApprovalList from './features/quest/components/ApprovalList';
import RewardList from './features/shop/components/RewardList';
import EquipmentShop from './features/shop/components/EquipmentShop';
import FamilyLog from './features/family/components/FamilyLog';
import FamilyParty from './features/family/components/FamilyParty';

// 確認モーダル（本来は別ファイル推奨ですが、一旦ここに維持）
const ConfirmModal = ({ mode, target, onConfirm, onCancel }) => {
  if (!target) return null;
  const isCancel = mode === 'cancel';
  const isPurchase = mode === 'purchase';

  let title = '確認';
  let message = '';
  let confirmBtnText = '実行する';
  let confirmBtnColor = 'bg-blue-600';

  if (isCancel) {
    title = '行動の取り消し';
    message = (
      <>
        「{target.quest_title || target.title}」<br />
        を取り消しますか？<br />
        <span className="text-xs text-gray-400 mt-2 block">
          (獲得した経験値やゴールドは没収されます)
        </span>
      </>
    );
    confirmBtnText = '取り消す';
    confirmBtnColor = 'bg-red-600';
  } else if (isPurchase) {
    title = '購入の確認';
    const cost = target.cost_gold || target.cost;
    message = (
      <>
        「{target.title}」<br />
        （{cost} G）を購入しますか？
      </>
    );
    confirmBtnText = 'はい';
    confirmBtnColor = 'bg-yellow-600 text-black';
  } else {
    title = '確認';
    message = (
      <>
        「{target.title}」<br />
        を達成しますか？
      </>
    );
    confirmBtnText = '達成する';
    confirmBtnColor = 'bg-blue-600';
  }

  return (
    <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4 animate-in fade-in">
      <div className="bg-slate-800 border border-slate-600 rounded-lg p-6 max-w-sm w-full shadow-2xl text-center">
        <h3 className={`text-xl font-bold mb-4 ${isCancel ? 'text-red-400' : isPurchase ? 'text-yellow-400' : 'text-blue-400'}`}>
          {title}
        </h3>
        <p className="text-white mb-6 leading-relaxed">
          {message}
        </p>
        <div className="flex gap-4 justify-center">
          <button onClick={onCancel} className="flex-1 py-3 bg-gray-600 rounded text-white font-bold hover:bg-gray-500">
            {isPurchase ? 'いいえ' : 'やめる'}
          </button>
          <button onClick={onConfirm} className={`flex-1 py-3 rounded text-white font-bold hover:opacity-80 ${confirmBtnColor}`}>
            {confirmBtnText}
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

  const [modalMode, setModalMode] = useState(null);
  const [targetHistory, setTargetHistory] = useState(null);
  const [targetItem, setTargetItem] = useState(null);
  const [messageModal, setMessageModal] = useState(null);

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

  const handleQuestClick = (quest) => {
    const qId = quest.quest_id || quest.id;
    let isInfinite = false;
    // .tsx化したQuestListからは _isInfinite が渡ってくる
    if (typeof quest._isInfinite !== 'undefined') {
      isInfinite = quest._isInfinite;
    } else {
      const type = quest.quest_type || quest.type;
      isInfinite = (type === 'infinite');
    }

    const isCompleted = completedQuests.some(cq => cq.user_id === currentUser?.user_id && cq.quest_id === qId);
    const isPending = pendingQuests.some(pq => pq.user_id === currentUser?.user_id && pq.quest_id === qId);

    if (isPending) return;

    if (isCompleted && !isInfinite) {
      const historyItem = completedQuests.find(cq => cq.user_id === currentUser?.user_id && cq.quest_id === qId);
      setTargetHistory(historyItem);
      setModalMode('cancel');
      return;
    }

    completeQuest(currentUser, quest);
  };

  const handleModalConfirm = async () => {
    if (modalMode === 'cancel' && targetHistory) {
      await cancelQuest(currentUser, targetHistory);
    } else if (modalMode === 'purchase' && targetItem) {
      const result = await buyReward(currentUser, targetItem);
      if (result && result.success) {
        setMessageModal({
          title: "お買い上げ！",
          message: `${result.reward.title} を\n手に入れた！`,
          icon: result.reward.icon || result.reward.icon_key || '🎁'
        });
      }
    }
    setModalMode(null);
    setTargetHistory(null);
    setTargetItem(null);
  };

  const handleModalCancel = () => {
    setModalMode(null);
    setTargetHistory(null);
    setTargetItem(null);
  };

  const handleApprove = (historyItem) => approveQuest(currentUser, historyItem);
  const handleReject = (historyItem) => rejectQuest(currentUser, historyItem);
  const handleBuyReward = (reward) => {
    setTargetItem(reward);
    setModalMode('purchase');
  };
  const handleBuyEquipment = (item) => buyEquipment(currentUser, item);
  const handleEquip = (item) => changeEquipment(currentUser, item);

  if (isLoading) return <div className="bg-black text-white h-screen flex items-center justify-center font-mono animate-pulse">LOADING ADVENTURE...</div>;

  return (
    <div className="min-h-screen bg-black font-mono text-white pb-8 select-none relative overflow-hidden">
      <LevelUpModal info={levelUpInfo} onClose={() => setLevelUpInfo(null)} />

      {messageModal && (
        <MessageModal
          title={messageModal.title}
          message={messageModal.message}
          icon={messageModal.icon}
          onClose={() => setMessageModal(null)}
        />
      )}

      {editingUser && (
        <AvatarUploader
          user={editingUser}
          onClose={() => setEditingUser(null)}
          onUploadComplete={() => { if (refreshData) refreshData(); }}
        />
      )}

      {modalMode && (
        <ConfirmModal
          mode={modalMode}
          target={modalMode === 'purchase' ? targetItem : targetHistory}
          onConfirm={handleModalConfirm}
          onCancel={handleModalCancel}
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
        {viewMode === 'user' && (
          <>
            <UserStatusCard
              user={currentUser}
              onAvatarClick={(user) => setEditingUser(user)}
            />

            {isParent && pendingQuests.length > 0 && activeTab === 'quest' && (
              <ApprovalList
                pendingQuests={pendingQuests}
                users={users}
                onApprove={handleApprove}
                onReject={handleReject}
              />
            )}

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

            <div className="min-h-[300px]">
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

        {viewMode === 'familyLog' && (
          <FamilyLog stats={familyStats} chronicle={chronicle} />
        )}

        {viewMode === 'party' && (
          <FamilyParty users={users} ownedEquipments={ownedEquipments} />
        )}
      </div>
    </div>
  );
}