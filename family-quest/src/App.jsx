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
import MessageModal from './components/ui/MessageModal';

// ★追加: 確認/取消モーダル
const ConfirmModal = ({ mode, target, onConfirm, onCancel }) => {
  if (!target) return null;
  const isCancel = mode === 'cancel';
  const isPurchase = mode === 'purchase'; // 追加

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
  } else if (isPurchase) { // 追加: 購入確認用の表示
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
    // デフォルト（クエスト完了確認など将来用）
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

  // キャンセル/確認モーダル用
  const [modalMode, setModalMode] = useState(null); // 'cancel' or null
  const [targetHistory, setTargetHistory] = useState(null);
  const [targetItem, setTargetItem] = useState(null);       // ★追加: 購入アイテム用
  const [messageModal, setMessageModal] = useState(null); // { title, message, icon }

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

  // ★修正: QuestList から渡された _isInfinite を優先的に使用
  const handleQuestClick = (quest) => {
    const qId = quest.quest_id || quest.id;

    // 1. _isInfinite (QuestList判定) があればそれを使う
    // 2. なければマスタデータの type / quest_type を確認する
    let isInfinite = false;
    if (typeof quest._isInfinite !== 'undefined') {
      isInfinite = quest._isInfinite;
    } else {
      const type = quest.quest_type || quest.type;
      isInfinite = (type === 'infinite');
    }

    const isCompleted = completedQuests.some(cq => cq.user_id === currentUser?.user_id && cq.quest_id === qId);
    const isPending = pendingQuests.some(pq => pq.user_id === currentUser?.user_id && pq.quest_id === qId);

    if (isPending) return; // 申請中は無視

    // 無限クエストなら、完了済み履歴があっても「キャンセル」ではなく「新規実施」として扱う
    if (isCompleted && !isInfinite) {
      // 通常クエストで完了済みの場合はキャンセル確認へ
      const historyItem = completedQuests.find(cq => cq.user_id === currentUser?.user_id && cq.quest_id === qId);
      setTargetHistory(historyItem);
      setModalMode('cancel');
      return;
    }

    // ★追加: 購入ボタンクリック時のハンドラ
    const handleBuyReward = (reward) => {
      setTargetItem(reward);
      setModalMode('purchase');
    };

    // 未完了、または無限クエストの場合は即実施
    completeQuest(currentUser, quest);
  };

  const handleModalConfirm = async () => {
    if (modalMode === 'cancel' && targetHistory) {
      await cancelQuest(currentUser, targetHistory);
    } else if (modalMode === 'purchase' && targetItem) {
      // ★修正: 購入処理の結果を受け取る
      const result = await buyReward(currentUser, targetItem);

      // 成功した場合のみ、リッチなメッセージモーダルを表示
      if (result && result.success) {
        setMessageModal({
          title: "お買い上げ！",
          message: `${result.reward.title} を\n手に入れた！`,
          icon: result.reward.icon || result.reward.icon_key || '🎁'
        });
      }
    }
    // 確認モーダルは閉じる
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
    console.log("Open Modal for:", reward); // デバッグ用ログ
    setTargetItem(reward);
    setModalMode('purchase');
  };
  const handleBuyEquipment = (item) => buyEquipment(currentUser, item);
  const handleEquip = (item) => changeEquipment(currentUser, item);

  if (isLoading) return <div className="bg-black text-white h-screen flex items-center justify-center font-mono animate-pulse">LOADING ADVENTURE...</div>;

  return (
    <div className="min-h-screen bg-black font-mono text-white pb-8 select-none relative overflow-hidden">
      <LevelUpModal info={levelUpInfo} onClose={() => setLevelUpInfo(null)} />


      {/* ★追加: 汎用メッセージモーダル (LevelUpModalの下あたりに配置) */}
      {messageModal && (
        <MessageModal
          title={messageModal.title}
          message={messageModal.message}
          icon={messageModal.icon}
          onClose={() => setMessageModal(null)}
        />
      )}

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
          // モードに応じてターゲットを切り替え
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
                  // ★修正: ユーザーオブジェクトではなく所持金を渡す
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