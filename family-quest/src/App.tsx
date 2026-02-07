import { useState } from 'react';
import { Sword, Shirt, ShoppingBag, Backpack, Scroll } from 'lucide-react';
import { INITIAL_USERS } from './lib/masterData';
import { useGameData } from './hooks/useGameData';
import { useSound } from './hooks/useSound';
import AdminDashboard from './features/admin/components/AdminDashboard';
import RewardList from './features/shop/components/RewardList';
import { InventoryList } from './features/shop/components/InventoryList';
import { GuildBoard } from './features/guild/components/GuildBoard';

import { Quest, QuestHistory, Reward, Equipment, BossEffect } from '@/types';

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
import EquipmentShop from './features/shop/components/EquipmentShop';
import FamilyLog from './features/family/components/FamilyLog';
import FamilyParty from './features/family/components/FamilyParty';
import BattleEffect from './components/ui/BattleEffect';


const ConfirmModal = ({
  mode, target, onConfirm, onCancel
}: {
  mode: 'cancel' | 'purchase' | 'complete' | 'equip_buy' | null,
  target: any,
  onConfirm: () => void,
  onCancel: () => void
}) => {
  if (!mode || !target) return null;

  const messages = {
    cancel: { title: 'クエストをやめる', text: `「${target.quest_title}」をやめますか？\n（ペナルティはありません）` },
    purchase: { title: 'アイテム購入', text: `「${target.title}」を ${target.cost_gold}G で買いますか？` },
    equip_buy: { title: '装備の購入', text: `「${target.name}」を ${target.cost}G で買いますか？` },
    complete: { title: 'クエスト完了', text: `「${target.title}」を完了にしますか？` },
  };
  const msg = messages[mode];

  return (
    <Modal isOpen={true} onClose={onCancel} title={msg.title}>
      <div className="p-4">
        <p className="whitespace-pre-wrap text-center mb-6">{msg.text}</p>
        <div className="flex gap-4 justify-center">
          <Button variant="secondary" onClick={onCancel}>キャンセル</Button>
          <Button variant="primary" onClick={onConfirm}>はい</Button>
        </div>
      </div>
    </Modal>
  );
};

function App() {
  const { play } = useSound();
  const [activeTab, setActiveTab] = useState<'quest' | 'shop' | 'equip' | 'inventory' | 'guild'>('quest');
  const [viewMode, setViewMode] = useState<'main' | 'admin' | 'familyLog' | 'party'>('main');
  const [currentUserIdx, setCurrentUserIdx] = useState(0);

  // モーダル状態
  const [confirmMode, setConfirmMode] = useState<'cancel' | 'purchase' | 'complete' | 'equip_buy' | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<any>(null);

  // 結果表示用
  const [levelUpInfo, setLevelUpInfo] = useState<any>(null);
  const [messageData, setMessageData] = useState<{ title: string, text: string, type?: 'success' | 'error' } | null>(null);
  const [bossEffect, setBossEffect] = useState<BossEffect | null>(null);

  // アバターアップロード
  const [isAvatarModalOpen, setIsAvatarModalOpen] = useState(false);

  const handleLevelUp = (info: any) => {
    setLevelUpInfo(info);
  };

  const {
    users, quests, rewards, completedQuests, pendingQuests,
    equipments, ownedEquipments, familyStats, chronicle, boss,
    isLoading,
    completeQuest, approveQuest, rejectQuest, cancelQuest, buyReward, buyEquipment, changeEquipment,
    refreshData, adminUpdateBoss
  } = useGameData(handleLevelUp);

  const currentUser = users[currentUserIdx] || INITIAL_USERS[0];

  // --- Handlers ---
  const handleUserChange = (idx: number) => {
    setCurrentUserIdx(idx);
    // ★修正③: ユーザーアイコンを押したら必ずメイン画面(User View)に戻す
    setViewMode('main');
    play('tap');
  };

  const handleQuestClick = (q: Quest | QuestHistory, isHistory: boolean) => {
    // 1. 履歴タブなど、明示的に履歴として渡された場合
    if (isHistory) {
      setConfirmTarget(q);
      setConfirmMode('cancel');
      play('select');
      return;
    }

    // 2. クエストリストから渡された場合 (q は Quest 型)
    // まず、無限クエストかどうかを判定（無限なら常に「完了」モードでOK）
    const isInfinite = (q as any)._isInfinite || (q as any).type === 'infinite' || (q as any).quest_type === 'infinite';

    if (isInfinite) {
      setConfirmTarget(q);
      setConfirmMode('complete');
      play('select');
      return;
    }

    // 3. 完了済み、または申請中リストにあるかを探す
    const qId = q.id || (q as any).quest_id;

    // 申請中を探す
    const pendingEntry = pendingQuests.find(pq =>
      pq.user_id === currentUser.user_id &&
      pq.quest_id === qId
    );

    // 完了済みを探す
    const completedEntry = completedQuests.find(cq =>
      cq.user_id === currentUser.user_id &&
      cq.quest_id === qId &&
      cq.status === 'approved'
    );

    const historyEntry = pendingEntry || completedEntry;

    if (historyEntry) {
      // 既に履歴がある（完了or申請中）なら「キャンセル（取り下げ）」モードにする
      // targetには Quest オブジェクトではなく、見つかった History オブジェクトを渡す
      // (ConfirmModalで target.quest_title を参照するため)
      // ※Historyオブジェクトに quest_title が結合されている前提ですが、
      //  もし不足している場合は q.title を補完する必要があります。
      setConfirmTarget({ ...historyEntry, quest_title: (q as any).title || (historyEntry as any).quest_title });
      setConfirmMode('cancel');
    } else {
      // 未実施なら「完了」モード
      setConfirmTarget(q);
      setConfirmMode('complete');
    }

    play('select');
  };

  const handleBuyReward = (r: Reward) => {
    setConfirmTarget(r);
    setConfirmMode('purchase');
    play('select');
  };

  const handleBuyEquipment = (e: Equipment) => {
    setConfirmTarget(e);
    setConfirmMode('equip_buy');
    play('select');
  };

  const handleEquip = async (e: Equipment) => {
    if (confirm(`「${e.name}」を装備しますか？`)) {
      const res = await changeEquipment(currentUser, e);
      if (res.success) {
        setMessageData({ title: "装備変更", text: "装備を変更しました！", type: "success" });
        play('select');
      }
    }
  };

  // --- Confirm Execution ---
  const executeConfirm = async () => {
    if (!confirmMode || !confirmTarget) return;

    let res: any = { success: false };

    if (confirmMode === 'complete') {
      res = await completeQuest(currentUser, confirmTarget);
      if (res.success) {
        if (res.status === 'pending') {
          setMessageData({ title: "申請完了", text: res.message || "親の承認待ちになりました", type: "success" });
        } else {
          if (res.bossEffect) setBossEffect(res.bossEffect);
        }
      }
    } else if (confirmMode === 'cancel') {
      res = await cancelQuest(currentUser, confirmTarget);
    } else if (confirmMode === 'purchase') {
      res = await buyReward(currentUser, confirmTarget);
      if (res.success) {
        setMessageData({ title: "購入完了", text: "アイテムを「もちもの」に入れました！", type: "success" });
        play('medal');
      }
    } else if (confirmMode === 'equip_buy') {
      res = await buyEquipment(currentUser, confirmTarget);
      if (res.success) {
        setMessageData({ title: "購入完了", text: "装備を手に入れました！", type: "success" });
        play('medal');
      }
    }

    if (!res.success && res.reason) {
      const reasons: { [key: string]: string } = {
        gold: "お金が足りません！",
        pending: "すでに申請中です",
        permission: "権限がありません",
        error: "エラーが発生しました"
      };
      setMessageData({ title: "エラー", text: reasons[res.reason] || "失敗しました", type: "error" });
      play('cancel');
    }

    setConfirmMode(null);
    setConfirmTarget(null);
  };

  // 承認・却下ハンドラ
  const handleApprove = async (history: QuestHistory) => {
    const res = await approveQuest(currentUser, history);
    if (res.success) {
      play('approve');
      if (res.bossEffect) setBossEffect(res.bossEffect);
    }
  };

  const handleReject = async (history: QuestHistory) => {
    if (confirm("本当に却下しますか？")) {
      const res = await rejectQuest(currentUser, history);
      if (res.success) play('cancel');
    }
  };

  const getHeaderViewMode = () => {
    if (viewMode === 'familyLog') return 'familyLog';
    if (viewMode === 'party') return 'party';
    return 'user';
  };

  if (isLoading) return <div className="p-10 text-center">Loading Family Quest...</div>;

  return (
    <div className="min-h-screen bg-gray-900 pb-20 font-sans text-gray-100">
      <Header
        users={users}
        currentUserIdx={currentUserIdx}
        viewMode={getHeaderViewMode()}
        onUserSwitch={handleUserChange}
        onPartySwitch={() => { setViewMode('party'); play('select'); }}
        onLogSwitch={() => { setViewMode('familyLog'); play('select'); }}
      />

      {/* ★修正①: max-w-md (スマホ幅) 固定を廃止し、md以上で幅広にする */}

      <div className="p-4 space-y-4 w-full max-w-md md:max-w-5xl mx-auto transition-all duration-300">

        {viewMode === 'admin' && (
          <AdminDashboard
            boss={boss}
            onUpdate={adminUpdateBoss}
            onClose={() => setViewMode('main')}
          />
        )}

        {viewMode === 'main' && (
          <>
            <UserStatusCard
              user={currentUser}
              onAvatarClick={() => setIsAvatarModalOpen(true)}
            />

            {(currentUser.user_id === 'dad' || currentUser.user_id === 'mom') && (
              <ApprovalList
                pendingQuests={pendingQuests}
                users={users}
                onApprove={handleApprove}
                onReject={handleReject}
              />
            )}

            <div className="flex gap-2 mb-4 bg-black p-2 rounded-lg border-2 border-white shadow-lg sticky top-16 z-10">
              {/* タブボタン：変更なし */}
              <button onClick={() => setActiveTab('quest')} className={`flex-1 py-2 text-xs font-bold rounded-lg flex flex-col items-center transition-all ${activeTab === 'quest' ? 'bg-blue-600 text-white shadow-md transform scale-105' : 'text-gray-200 hover:bg-gray-900'}`}>
                <Sword size={20} className="mb-1" /> クエスト
              </button>

              <button onClick={() => setActiveTab('shop')} className={`flex-1 py-2 text-xs font-bold rounded-lg flex flex-col items-center transition-all ${activeTab === 'shop' ? 'bg-orange-500 text-white shadow-md transform scale-105' : 'text-gray-200 hover:bg-gray-900'}`}>
                <ShoppingBag size={20} className="mb-1" /> ごほうび
              </button>
              <button onClick={() => setActiveTab('equip')} className={`flex-1 py-2 text-xs font-bold rounded-lg flex flex-col items-center transition-all ${activeTab === 'equip' ? 'bg-green-600 text-white shadow-md transform scale-105' : 'text-gray-200 hover:bg-gray-900'}`}>
                <Shirt size={20} className="mb-1" /> そうび
              </button>
              <button onClick={() => setActiveTab('inventory')} className={`flex-1 py-2 text-xs font-bold rounded-lg flex flex-col items-center transition-all ${activeTab === 'inventory' ? 'bg-yellow-500 text-white shadow-md transform scale-105' : 'text-gray-200 hover:bg-gray-900'}`}>
                <Backpack size={20} className="mb-1" /> もちもの
              </button>
              {/* ★追加: ギルドタブ */}
              <button
                onClick={() => { play('tap'); setActiveTab('guild'); }} // cursor -> tap に変更
                className={`flex-1 py-2 text-xs font-bold rounded-lg flex flex-col items-center transition-all ${activeTab === 'guild' ? 'bg-amber-600 text-white' : 'text-gray-400 hover:bg-gray-700'
                  }`}
              >
                <Scroll size={20} className="mb-1" /> ギルド（開発中）
              </button>
            </div>

            <div className="min-h-[300px] animate-fade-in">
              {activeTab === 'quest' && (
                <QuestList
                  quests={quests}
                  completedQuests={completedQuests}
                  pendingQuests={pendingQuests}
                  currentUser={currentUser}
                  onQuestClick={(q) => handleQuestClick(q, false)}
                />
              )}

              {/* ★追加: ギルド画面の表示 */}
              {activeTab === 'guild' && (
                <div className="animate-fade-in">
                  {/* userId プロパティを追加 */}
                  <GuildBoard userId={currentUser.user_id} />
                </div>
              )}

              {activeTab === 'shop' && (
                <div className="animate-slide-in-right">

                  <RewardList
                    rewards={rewards}
                    userGold={currentUser.gold}
                    onBuy={handleBuyReward}
                    currentUser={currentUser}
                  />
                </div>
              )}

              {activeTab === 'equip' && (
                <div className="animate-slide-in-right">

                  <EquipmentShop
                    equipments={equipments}
                    ownedEquipments={ownedEquipments}
                    currentUser={currentUser}
                    onBuy={handleBuyEquipment}
                    onEquip={handleEquip}
                  />
                </div>
              )}

              {activeTab === 'inventory' && (
                <div className="animate-slide-in-right">

                  <InventoryList userId={currentUser.user_id} />
                </div>
              )}
            </div>
          </>
        )}

        {viewMode === 'familyLog' && (
          <FamilyLog stats={familyStats} chronicle={chronicle} />
        )}

        {viewMode === 'party' && (
          <FamilyParty users={users} ownedEquipments={ownedEquipments} boss={boss} />
        )}

      </div>

      <ConfirmModal
        mode={confirmMode}
        target={confirmTarget}
        onConfirm={executeConfirm}
        onCancel={() => { setConfirmMode(null); play('cancel'); }}
      />

      <LevelUpModal
        info={levelUpInfo}
        onClose={() => setLevelUpInfo(null)}
      />

      {messageData && (
        <MessageModal
          title={messageData.title}
          message={messageData.text}
          onClose={() => setMessageData(null)}
        />
      )}

      {isAvatarModalOpen && (
        <AvatarUploader
          user={currentUser}
          onClose={() => setIsAvatarModalOpen(false)}
          onUploadComplete={() => {
            refreshData();
            setMessageData({ title: "変更完了", text: "アバターを変更しました！", type: "success" });
          }}
        />
      )}

      {bossEffect && (
        <BattleEffect
          effect={bossEffect}
          boss={boss}
          onClose={() => {
            setBossEffect(null);
            refreshData();
          }}
        />
      )}

    </div>
  );
}

export default App;