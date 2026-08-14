import { useState } from 'react';
import { Sword, ShoppingBag } from 'lucide-react';
import { INITIAL_USERS } from './lib/masterData';
import { useGameData, LevelUpInfo } from './hooks/useGameData';
import { useSound } from './hooks/useSound';
import RewardShop from './features/shop/components/RewardShop';

import { Quest, QuestHistory, Reward, User } from '@/types';
import { getQuestLockState } from './features/quest/hooks/useQuestStatus';

// 保護者判定は quest_users.role ('role_adult'/'role_child') を唯一の判定基準とする。
// ★注意: これはクライアント側のUI上の配慮（隠しボタンを子どもに見せないため）にすぎず、
// セキュリティ境界ではない。バックエンドは現状どのuser_idでも自称できてしまうため、
// 本当のアクセス制御はバックエンド側で別途実装される必要がある。
const isParentUser = (user: User) => user.role === 'role_adult';


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
import FamilyLog from './features/family/components/FamilyLog';

// ConfirmModal の target に渡りうる型。モードごとに実際に持っているプロパティが異なるため、
// メッセージ生成はモードごとに個別にキャストして組み立てる（getMessage 内）。
type ConfirmTarget = Quest | QuestHistory | Reward;

// useGameData.ts の completeQuest/cancelQuest/buyReward/rejectQuest
// ラッパー関数群の戻り値をまとめて受け取るための型（各関数は success 以外のフィールドが少しずつ異なる）
interface ActionResult {
  success: boolean;
  status?: string;
  message?: string;
  earnedMedals?: number;
  leveledUp?: boolean;
  newGold?: number;
  reward?: Reward;
  reason?: string;
  detail?: string;
}

const ConfirmModal = ({
  mode, target, onConfirm, onCancel
}: {
  mode: 'cancel' | 'purchase' | 'complete' | 'reject' | null,
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
      case 'complete': {
        const t = target as Quest;
        return { title: 'クエスト完了', text: `「${t.title}」を完了にしますか？` };
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
  const [activeTab, setActiveTab] = useState<'quest' | 'shop'>('quest');
  const [viewMode, setViewMode] = useState<'main' | 'familyLog'>('main');
  const [currentUserIdx, setCurrentUserIdx] = useState(0);

  // モーダル状態
  const [confirmMode, setConfirmMode] = useState<'cancel' | 'purchase' | 'complete' | 'reject' | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<ConfirmTarget | null>(null);

  // 結果表示用
  const [levelUpInfo, setLevelUpInfo] = useState<LevelUpInfo | null>(null);
  const [messageData, setMessageData] = useState<{ title: string, text: string, type?: 'success' | 'error' } | null>(null);

  // アバターアップロード
  const [isAvatarModalOpen, setIsAvatarModalOpen] = useState(false);

  const handleLevelUp = (info: LevelUpInfo) => {
    setLevelUpInfo(info);
  };

  const {
    users, quests, rewards, completedQuests, pendingQuests,
    familyStats, chronicle,
    pendingInventory,
    isLoading,
    completeQuest, approveQuest, rejectQuest, cancelQuest, buyReward,
    refreshData,
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

  // --- Confirm Execution ---
  const executeConfirm = async () => {
    if (!confirmMode || !confirmTarget) return;

    let res: ActionResult = { success: false };

    if (confirmMode === 'complete') {
      res = await completeQuest(currentUser, confirmTarget as Quest);
      if (res.success) {
        if (res.status === 'pending') {
          setMessageData({ title: "申請完了", text: res.message || "親の承認待ちになりました", type: "success" });
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
        onLogSwitch={() => { setViewMode('familyLog'); play('select'); }}
      />

      {/* ★修正①: max-w-md (スマホ幅) 固定を廃止し、md以上で幅広にする */}

      <div className="p-4 space-y-4 w-full max-w-md md:max-w-5xl mx-auto transition-all duration-300">

        {viewMode === 'main' && (
          <>
            <UserStatusCard
              user={currentUser}
              onAvatarClick={() => setIsAvatarModalOpen(true)}
            />

            {isParentUser(currentUser) && (
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
                <Sword size={20} className="mb-1" /> クエスト
              </button>

              <button onClick={() => setActiveTab('shop')} className={`flex-1 min-w-[4rem] py-2 text-xs font-bold rounded-lg flex flex-col items-center transition-all ${activeTab === 'shop' ? 'bg-orange-500 text-white shadow-md transform scale-105' : 'text-gray-200 hover:bg-gray-900'}`}>
                <ShoppingBag size={20} className="mb-1" /> ごほうび
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

              {activeTab === 'shop' && (
                <div className="animate-slide-in-right">
                  <RewardShop
                    rewards={rewards}
                    currentUser={currentUser}
                    onBuy={handleBuyReward}
                  />
                </div>
              )}
            </div>
          </>
        )}

        {viewMode === 'familyLog' && (
          <FamilyLog stats={familyStats} chronicle={chronicle} />
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

    </div>
  );
}

export default App;