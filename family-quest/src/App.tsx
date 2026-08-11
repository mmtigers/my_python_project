import { useState } from 'react';
import { Sword, Shirt, ShoppingBag, Backpack, Scroll, Sparkles } from 'lucide-react'; // Sparkles追加
import { INITIAL_USERS } from './lib/masterData';
import { useGameData, LevelUpInfo } from './hooks/useGameData';
import { useSound } from './hooks/useSound';
import AdminDashboard from './features/admin/components/AdminDashboard';
import RewardList from './features/shop/components/RewardList';
import { InventoryList } from './features/shop/components/InventoryList';
import { GuildBoard } from './features/guild/components/GuildBoard';

import { Quest, QuestHistory, Reward, Equipment, BossEffect } from '@/types';
import { getQuestLockState } from './features/quest/hooks/useQuestStatus';

// 保護者ユーザーID一覧（MY_HOME_SYSTEM/services/quest_service.py の PARENT_IDS と一致させる）
// ★注意: これはクライアント側のUI上の配慮（隠しボタンを子どもに見せないため）にすぎず、
// セキュリティ境界ではない。バックエンドは現状どのuser_idでも自称できてしまうため、
// 本当のアクセス制御はバックエンド側で別途実装される必要がある。
const PARENT_USER_IDS = ['dad', 'mom'];


// UI Components
import LevelUpModal from './components/ui/LevelUpModal';
import Header from './components/layout/Header';
import AvatarUploader from './components/ui/AvatarUploader';
import MessageModal from './components/ui/MessageModal';
import { Button } from './components/ui/Button';
import { Modal } from './components/ui/Modal';
import { FamilyMileageCard } from './features/family/components/FamilyMileageCard'; // ★これを追加



import UserStatusCard from './features/family/components/UserStatusCard';
import QuestList from './features/quest/components/QuestList';
import ApprovalList from './features/quest/components/ApprovalList';
import EquipmentShop from './features/shop/components/EquipmentShop';
import FamilyLog from './features/family/components/FamilyLog';
import FamilyParty from './features/family/components/FamilyParty';
import BattleEffect from './components/ui/BattleEffect';
import { WeeklyTrends } from './features/family/components/WeeklyTrends';

// ConfirmModal の target に渡りうる型。モードごとに実際に持っているプロパティが異なるため、
// メッセージ生成はモードごとに個別にキャストして組み立てる（getMessage 内）。
type ConfirmTarget = Quest | QuestHistory | Reward | Equipment;

// useGameData.ts の completeQuest/cancelQuest/buyReward/buyEquipment/changeEquipment/rejectQuest
// ラッパー関数群の戻り値をまとめて受け取るための型（各関数は success 以外のフィールドが少しずつ異なる）
interface ActionResult {
  success: boolean;
  status?: string;
  message?: string;
  earnedMedals?: number;
  leveledUp?: boolean;
  bossEffect?: BossEffect;
  newGold?: number;
  reward?: Reward;
  item?: Equipment;
  reason?: string;
  detail?: string;
}

const ConfirmModal = ({
  mode, target, onConfirm, onCancel
}: {
  mode: 'cancel' | 'purchase' | 'complete' | 'equip_buy' | 'equip' | 'reject' | null,
  target: ConfirmTarget | null,
  onConfirm: () => void,
  onCancel: () => void
}) => {
  if (!mode || !target) return null;

  const getMessage = (): { title: string; text: string } => {
    switch (mode) {
      case 'cancel': {
        const t = target as QuestHistory;
        return { title: 'クエストをやめる', text: `「${t.quest_title}」をやめますか？\n（ペナルティはありません）` };
      }
      case 'purchase': {
        const t = target as Reward;
        return { title: 'アイテム購入', text: `「${t.title}」を ${t.cost_gold}G で買いますか？` };
      }
      case 'equip_buy': {
        const t = target as Equipment;
        return { title: '装備の購入', text: `「${t.name}」を ${t.cost}G で買いますか？` };
      }
      case 'complete': {
        const t = target as Quest;
        return { title: 'クエスト完了', text: `「${t.title}」を完了にしますか？` };
      }
      case 'equip': {
        const t = target as Equipment;
        return { title: '装備変更', text: `「${t.name}」を装備しますか？` };
      }
      case 'reject':
        return { title: '却下確認', text: '本当に却下しますか？' };
    }
  };
  const msg = getMessage();

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
  const [activeTab, setActiveTab] = useState<'quest' | 'special_quest' | 'shop' | 'equip' | 'inventory' | 'guild'>('quest');
  const [viewMode, setViewMode] = useState<'main' | 'admin' | 'familyLog' | 'party' | 'trends'>('main');
  const [currentUserIdx, setCurrentUserIdx] = useState(0);

  // モーダル状態
  const [confirmMode, setConfirmMode] = useState<'cancel' | 'purchase' | 'complete' | 'equip_buy' | 'equip' | 'reject' | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<ConfirmTarget | null>(null);

  // 結果表示用
  const [levelUpInfo, setLevelUpInfo] = useState<LevelUpInfo | null>(null);
  const [messageData, setMessageData] = useState<{ title: string, text: string, type?: 'success' | 'error' } | null>(null);
  const [bossEffect, setBossEffect] = useState<BossEffect | null>(null);

  // アバターアップロード
  const [isAvatarModalOpen, setIsAvatarModalOpen] = useState(false);

  const handleLevelUp = (info: LevelUpInfo) => {
    setLevelUpInfo(info);
  };

  const {
    users, quests, rewards, completedQuests, pendingQuests,
    equipments, ownedEquipments, familyStats, chronicle, boss,
    familyMileage, pendingInventory,
    isLoading,
    completeQuest, approveQuest, rejectQuest, cancelQuest, buyReward, buyEquipment, changeEquipment,
    refreshData, adminUpdateBoss, adminUpdateFamilyMileage
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
    // ロック/申請中/完了の判定は useQuestStatus と共通の getQuestLockState に集約
    const { isInfinite, pendingEntry, completedEntry } =
      getQuestLockState(q as Quest, currentUser, completedQuests, pendingQuests);

    // まず、無限クエストかどうかを判定（無限なら常に「完了」モードでOK）
    if (isInfinite) {
      setConfirmTarget(q);
      setConfirmMode('complete');
      play('select');
      return;
    }

    // 3. 完了済み、または申請中リストにあるかを探す
    const historyEntry = pendingEntry || completedEntry;

    if (historyEntry) {
      // 既に履歴がある（完了or申請中）なら「キャンセル（取り下げ）」モードにする
      // targetには Quest オブジェクトではなく、見つかった History オブジェクトを渡す
      // (ConfirmModalで target.quest_title を参照するため)
      // ※Historyオブジェクトに quest_title が結合されている前提ですが、
      //  もし不足している場合は q.title を補完する必要があります。
      setConfirmTarget({ ...historyEntry, quest_title: ('title' in q ? q.title : undefined) || historyEntry.quest_title });
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

  const handleEquip = (e: Equipment) => {
    setConfirmTarget(e);
    setConfirmMode('equip');
    play('select');
  };

  // --- Confirm Execution ---
  const executeConfirm = async () => {
    if (!confirmMode || !confirmTarget) return;

    let res: ActionResult = { success: false };

    if (confirmMode === 'complete') {
      res = await completeQuest(currentUser, confirmTarget as Quest);
      if (res.success) {
        if (res.status === 'pending') {
          setMessageData({ title: "申請完了", text: res.message || "親の承認待ちになりました", type: "success" });
        } else {
          if (res.bossEffect) setBossEffect(res.bossEffect);
        }
      }
    } else if (confirmMode === 'cancel') {
      res = await cancelQuest(currentUser, confirmTarget as QuestHistory);
    } else if (confirmMode === 'purchase') {
      res = await buyReward(currentUser, confirmTarget as Reward);
      if (res.success) {
        setMessageData({ title: "購入完了", text: "アイテムを「もちもの」に入れました！", type: "success" });
        play('medal');
      }
    } else if (confirmMode === 'equip_buy') {
      res = await buyEquipment(currentUser, confirmTarget as Equipment);
      if (res.success) {
        setMessageData({ title: "購入完了", text: "装備を手に入れました！", type: "success" });
        play('medal');
      }
    } else if (confirmMode === 'equip') {
      res = await changeEquipment(currentUser, confirmTarget as Equipment);
      if (res.success) {
        setMessageData({ title: "装備変更", text: "装備を変更しました！", type: "success" });
        play('select');
      }
    } else if (confirmMode === 'reject') {
      res = await rejectQuest(currentUser, confirmTarget as QuestHistory);
      if (res.success) {
        play('cancel');
      } else {
        // ★却下専用のエラー文言（元の handleReject の window.confirm/reasons ロジックを踏襲）
        const reasons: { [key: string]: string } = {
          permission: "権限がありません",
          error: "エラーが発生しました"
        };
        const text = res.detail || (res.reason && reasons[res.reason]) || "却下に失敗しました";
        setMessageData({ title: "エラー", text, type: "error" });
        play('cancel');
        setConfirmMode(null);
        setConfirmTarget(null);
        return;
      }
    }

    if (!res.success) {
      const reasons: { [key: string]: string } = {
        gold: "お金が足りません！",
        pending: "すでに申請中です",
        permission: "権限がありません",
        error: "エラーが発生しました"
      };
      // バックエンドが具体的なエラー内容(detail)を返している場合はそれを優先表示する
      const text = res.detail || (res.reason && reasons[res.reason]) || "失敗しました";
      setMessageData({ title: "エラー", text, type: "error" });
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
    } else {
      const reasons: { [key: string]: string } = {
        permission: "権限がありません",
        error: "エラーが発生しました"
      };
      const text = res.detail || (res.reason && reasons[res.reason]) || "承認に失敗しました";
      setMessageData({ title: "エラー", text, type: "error" });
      play('cancel');
    }
  };

  const handleReject = (history: QuestHistory) => {
    setConfirmTarget(history);
    setConfirmMode('reject');
    play('select');
  };

  const getHeaderViewMode = () => {
    if (viewMode === 'familyLog') return 'familyLog';
    if (viewMode === 'party') return 'party';
    if (viewMode === 'trends') return 'trends';
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
        onTrendsSwitch={() => { setViewMode('trends'); play('select'); }}
        onAdminOpen={
          PARENT_USER_IDS.includes(currentUser.user_id)
            ? () => { setViewMode('admin'); play('select'); }
            : undefined
        }
      />

      {/* ★修正①: max-w-md (スマホ幅) 固定を廃止し、md以上で幅広にする */}

      <div className="p-4 space-y-4 w-full max-w-md md:max-w-5xl mx-auto transition-all duration-300">

        {viewMode === 'admin' && (
          <AdminDashboard
            boss={boss}
            onUpdate={adminUpdateBoss}
            onUpdateMileage={adminUpdateFamilyMileage}
            onClose={() => setViewMode('main')}
          />
        )}

        {viewMode === 'main' && (
          <>
            {/* 共有目標（ファミリーマイレージ）表示領域 */}
            {/* ★ 修正: ダミーデータ(window.__familyMileageData)ではなく、実際の familyMileage を渡す */}
            <FamilyMileageCard mileage={familyMileage} />

            <UserStatusCard
              user={currentUser}
              onAvatarClick={() => setIsAvatarModalOpen(true)}
            />

            {PARENT_USER_IDS.includes(currentUser.user_id) && (
              <ApprovalList
                pendingQuests={pendingQuests}
                pendingItems={pendingInventory}
                users={users}
                currentUser={currentUser}
                onApprove={handleApprove}
                onReject={handleReject}
              />
            )}

            <div className="flex gap-2 mb-4 bg-black p-2 rounded-lg border-2 border-white shadow-lg sticky top-16 z-10 overflow-x-auto">
              <button onClick={() => setActiveTab('quest')} className={`flex-1 min-w-[4rem] py-2 text-xs font-bold rounded-lg flex flex-col items-center transition-all ${activeTab === 'quest' ? 'bg-blue-600 text-white shadow-md transform scale-105' : 'text-gray-400 hover:bg-gray-800'}`}>
                <Sword size={20} className="mb-1" /> 通常クエスト
              </button>
              {/* ★追加: 特別クエストタブ */}
              <button onClick={() => setActiveTab('special_quest')} className={`flex-1 min-w-[4rem] py-2 text-xs font-bold rounded-lg flex flex-col items-center transition-all ${activeTab === 'special_quest' ? 'bg-purple-600 text-white shadow-md transform scale-105' : 'text-gray-400 hover:bg-gray-800'}`}>
                <Sparkles size={20} className="mb-1" /> 特別クエスト
              </button>

              <button onClick={() => setActiveTab('shop')} className={`flex-1 min-w-[4rem] py-2 text-xs font-bold rounded-lg flex flex-col items-center transition-all ${activeTab === 'shop' ? 'bg-orange-500 text-white shadow-md transform scale-105' : 'text-gray-200 hover:bg-gray-900'}`}>
                <ShoppingBag size={20} className="mb-1" /> ごほうび
              </button>
              <button onClick={() => setActiveTab('equip')} className={`flex-1 min-w-[4rem] py-2 text-xs font-bold rounded-lg flex flex-col items-center transition-all ${activeTab === 'equip' ? 'bg-green-600 text-white shadow-md transform scale-105' : 'text-gray-200 hover:bg-gray-900'}`}>
                <Shirt size={20} className="mb-1" /> そうび
              </button>
              <button onClick={() => setActiveTab('inventory')} className={`flex-1 min-w-[4rem] py-2 text-xs font-bold rounded-lg flex flex-col items-center transition-all ${activeTab === 'inventory' ? 'bg-yellow-500 text-white shadow-md transform scale-105' : 'text-gray-200 hover:bg-gray-900'}`}>
                <Backpack size={20} className="mb-1" /> もちもの
              </button>
              {/* ★追加: ギルドタブ */}
              <button
                onClick={() => { play('tap'); setActiveTab('guild'); }} // cursor -> tap に変更
                className={`flex-1 min-w-[4rem] py-2 text-xs font-bold rounded-lg flex flex-col items-center transition-all ${activeTab === 'guild' ? 'bg-amber-600 text-white' : 'text-gray-400 hover:bg-gray-700'
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
                  isDaily={true} // ★追加: 日常クエストのみ
                />
              )}
              {activeTab === 'special_quest' && (
                <QuestList
                  quests={quests}
                  completedQuests={completedQuests}
                  pendingQuests={pendingQuests}
                  currentUser={currentUser}
                  onQuestClick={(q) => handleQuestClick(q, false)}
                  isDaily={false} // ★追加: 特別クエストのみ
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

        {viewMode === 'trends' && (
          <WeeklyTrends />
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