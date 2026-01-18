import React, { useState } from 'react';
import { Sword, Shirt, ShoppingBag } from 'lucide-react';
import { INITIAL_USERS } from './lib/masterData';
import { useGameData } from './hooks/useGameData';
import { useSound } from './hooks/useSound'; // 追加: 音を鳴らすため
import { User, Quest, QuestHistory, Reward, Equipment } from '@/types';

// UI Components
import LevelUpModal from './components/ui/LevelUpModal';
import Header from './components/layout/Header';
import AvatarUploader from './components/ui/AvatarUploader';
import MessageModal from './components/ui/MessageModal';
import { Button } from './components/ui/Button';
import { Modal } from './components/ui/Modal';

import UserStatusCard from './features/family/components/UserStatusCard';
import QuestList from './features/quest/components/QuestList';
import ApprovalList from './features/quest/components/ApprovalList';
import RewardList from './features/shop/components/RewardList';
import EquipmentShop from './features/shop/components/EquipmentShop';
import FamilyLog from './features/family/components/FamilyLog';
import FamilyParty from './features/family/components/FamilyParty';

const ConfirmModal = ({
  mode, target, onConfirm, onCancel
}: {
  mode: 'cancel' | 'purchase' | 'complete' | 'equip_buy' | null,
  target: any,
  onConfirm: () => void,
  onCancel: () => void
}) => {
  if (!target) return null;
  const isCancel = mode === 'cancel';
  const isPurchase = mode === 'purchase';
  const isEquipBuy = mode === 'equip_buy';

  let title = '確認';
  let message: React.ReactNode = '';
  let confirmBtnVariant: 'primary' | 'danger' | 'secondary' = 'primary';
  let confirmBtnText = '実行する';

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
    confirmBtnVariant = 'danger';
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
  } else if (isEquipBuy) {
    title = '装備の購入';
    message = (
      <>
        「{target.name}」<br />
        （{target.cost} G）を購入しますか？
      </>
    );
    confirmBtnText = '買う！';
  }

  return (
    <Modal isOpen={true} onClose={onCancel} title={title}>
      <div className="text-center mb-6 leading-relaxed font-bold">
        {message}
      </div>
      <div className="flex gap-4 justify-center">
        <Button onClick={onCancel} variant="secondary" className="flex-1">
          {isPurchase || isEquipBuy ? 'いいえ' : 'やめる'}
        </Button>
        <Button onClick={onConfirm} variant={confirmBtnVariant} className="flex-1">
          {confirmBtnText}
        </Button>
      </div>
    </Modal>
  );
};

export default function App() {
  const [viewMode, setViewMode] = useState<'user' | 'party' | 'familyLog'>('user');
  const [activeTab, setActiveTab] = useState<'quest' | 'shop' | 'equip'>('quest');
  const [currentUserIdx, setCurrentUserIdx] = useState(0);
  const [levelUpInfo, setLevelUpInfo] = useState<any>(null);
  const [editingUser, setEditingUser] = useState<User | null>(null);

  const [modalMode, setModalMode] = useState<'cancel' | 'purchase' | 'complete' | 'equip_buy' | null>(null);
  const [targetHistory, setTargetHistory] = useState<QuestHistory | null>(null);
  const [targetItem, setTargetItem] = useState<any>(null);
  const [messageModal, setMessageModal] = useState<{ title: string, message: string, icon?: string } | null>(null);

  const { play } = useSound(); // 音機能を利用

  const {
    users, quests, rewards, completedQuests, pendingQuests,
    equipments, ownedEquipments, familyStats, chronicle, isLoading,
    boss, // ★修正: ここで boss を取り出す
    completeQuest, approveQuest, rejectQuest, cancelQuest,
    buyReward, buyEquipment, changeEquipment, refreshData
  } = useGameData((info: any) => setLevelUpInfo(info));

  const currentUser = users?.[currentUserIdx] || INITIAL_USERS?.[0] || {};
  const isParent = ['dad', 'mom'].includes(currentUser?.user_id);

  const handleUserSwitch = (idx: number) => {
    setViewMode('user');
    setCurrentUserIdx(idx);
  };

  const handleQuestClick = async (quest: Quest) => {
    const qId = quest.quest_id || quest.id;
    let isInfinite = false;
    if (typeof quest._isInfinite !== 'undefined') {
      isInfinite = quest._isInfinite;
    } else {
      const type = quest.quest_type || quest.type;
      isInfinite = (type === 'infinite');
    }

    const isCompleted = completedQuests.some(cq => cq.user_id === currentUser?.user_id && cq.quest_id === qId);
    const isPending = pendingQuests.some(pq => pq.user_id === currentUser?.user_id && pq.quest_id === qId);

    // 申請中は無視
    if (isPending) {
      setMessageModal({ title: "確認中", message: "親の承認待ちです", icon: "⏳" });
      return;
    }

    // 完了済み(かつ無限じゃない)ならキャンセル確認へ
    if (isCompleted && !isInfinite) {
      const historyItem = completedQuests.find(cq => cq.user_id === currentUser?.user_id && cq.quest_id === qId);
      if (historyItem) {
        setTargetHistory(historyItem);
        setModalMode('cancel');
      }
      return;
    }

    // クエスト完了処理を実行
    const result = await completeQuest(currentUser, quest);

    // 結果に応じた処理
    if (!result.success) {
      if (result.reason === 'pending') {
        setMessageModal({ title: "確認中", message: "承認されるまでお待ちください", icon: "⏳" });
      } else {
        // エラー時は必要ならメッセージを
        console.error("Quest completion failed");
      }
    } else {
      // メダル獲得時
      if (result.earnedMedals > 0) {
        play('medal'); // メダル音
        setMessageModal({
          title: "ラッキー！！",
          message: "ちいさなメダル を見つけた！",
          icon: "🏅"
        });
      }
    }
  };

  const handleModalConfirm = async () => {
    // キャンセル処理
    if (modalMode === 'cancel' && targetHistory) {
      await cancelQuest(currentUser, targetHistory);
    }
    // ごほうび購入
    else if (modalMode === 'purchase' && targetItem) {
      const result = await buyReward(currentUser, targetItem);
      if (result.success) {
        play('medal'); // 購入成功音(仮)
        setMessageModal({
          title: "お買い上げ！",
          message: `${result.reward.title} を\n手に入れた！`,
          icon: result.reward.icon || '🎁'
        });
      } else if (result.reason === 'gold') {
        setMessageModal({ title: "資金不足", message: "ゴールドが足りません！", icon: "💸" });
      }
    }
    // 装備購入
    else if (modalMode === 'equip_buy' && targetItem) {
      const result = await buyEquipment(currentUser, targetItem);
      if (result.success) {
        play('medal'); // 購入音
        setMessageModal({
          title: "装備ゲット！",
          message: `${result.item.name} を\n手に入れた！`,
          icon: "⚔️"
        });
      } else if (result.reason === 'gold') {
        setMessageModal({ title: "資金不足", message: "ゴールドが足りません！", icon: "💸" });
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

  // 各ハンドラ
  const handleApprove = async (historyItem: QuestHistory) => {
    const res = await approveQuest(currentUser, historyItem);
    if (res.success) play('approve');
  };

  const handleReject = async (historyItem: QuestHistory) => {
    await rejectQuest(currentUser, historyItem);
  };

  const handleBuyReward = (reward: Reward) => {
    setTargetItem(reward);
    setModalMode('purchase');
  };

  const handleBuyEquipment = (item: Equipment) => {
    setTargetItem(item);
    setModalMode('equip_buy');
  };

  const handleEquip = async (item: Equipment) => {
    const res = await changeEquipment(currentUser, item);
    if (res.success) play('tap');
  };

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
          target={(modalMode === 'purchase' || modalMode === 'equip_buy') ? targetItem : targetHistory}
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

      <div className="p-4 space-y-4 w-full max-w-md md:max-w-5xl mx-auto transition-all duration-300">
        {viewMode === 'user' && (
          <>
            <UserStatusCard
              user={currentUser}
              onAvatarClick={(user: User) => setEditingUser(user)}
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
          <FamilyParty users={users} ownedEquipments={ownedEquipments} boss={boss} />)}
      </div>
    </div>
  );
}