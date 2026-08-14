import { useState } from 'react';
import { Sword, ShoppingBag } from 'lucide-react';
import { INITIAL_USERS } from './lib/masterData';
import { useGameData, LevelUpInfo } from './hooks/useGameData';
import { useSound } from './hooks/useSound';
import { useLayoutMode } from './hooks/useLayoutMode';
import RewardShop from './features/shop/components/RewardShop';
import FamilyDashboard from './features/family/components/FamilyDashboard';

import { Quest, QuestHistory, Reward, User } from '@/types';
import { getQuestLockState } from './features/quest/hooks/useQuestStatus';

// 保護者判定は quest_users.role ('role_adult'/'role_child') を唯一の判定基準とする。
// ★注意: これはクライアント側のUI上の配慮（隠しボタンを子どもに見せないため）にすぎず、
// セキュリティ境界ではない。バックエンドは現状どのuser_idでも自称できてしまうため、
// 本当のアクセス制御はバックエンド側で別途実装される必要がある。
const isParentUser = (user: User) => user.role === 'role_adult';

// 承認・却下の記録名義に使う代表の親ユーザーを返す。誰が実際にボタンを押したかは
// 区別せず「親」として固定で記録する(要件5)。
const getRepresentativeParent = (allUsers: User[]): User => {
  const adult = allUsers.find(u => u.role === 'role_adult');
  return adult || allUsers[0] || INITIAL_USERS[0];
};


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
// ★要件9: クエストの完了/取り消しは確認ダイアログを挟まないワンタップ操作に変更したため、
// ここで確認を挟むのはゴールドを消費する「購入」と、親向けの「却下」のみ(誤操作の影響が大きいため)。
type ConfirmTarget = QuestHistory | Reward;

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

const ERROR_REASON_MESSAGES: { [key: string]: string } = {
  gold: "お金が足りません！",
  pending: "すでに申請中です",
  permission: "権限がありません",
  error: "エラーが発生しました",
};

// バックエンドが具体的なエラー内容(detail)を返している場合はそれを優先表示する
const resolveErrorText = (res: ActionResult, fallback: string): string =>
  res.detail || (res.reason && ERROR_REASON_MESSAGES[res.reason]) || fallback;

const ConfirmModal = ({
  mode, target, onConfirm, onCancel
}: {
  mode: 'purchase' | 'reject' | null,
  target: ConfirmTarget | null,
  onConfirm: () => void,
  onCancel: () => void
}) => {
  if (!mode || !target) return null;

  const getMessage = (): { title: string; text: string } => {
    switch (mode) {
      case 'purchase': {
        const t = target as Reward;
        return { title: 'アイテム購入', text: `「${t.title}」を ${t.cost_gold}G で買いますか？` };
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
  const layoutMode = useLayoutMode();
  const [activeTab, setActiveTab] = useState<'quest' | 'shop'>('quest');
  const [viewMode, setViewMode] = useState<'main' | 'familyLog'>('main');
  const [currentUserIdx, setCurrentUserIdx] = useState(0);

  // モーダル状態 (購入・却下のみ。完了・取消はワンタップ即時実行のため確認を挟まない)
  const [confirmMode, setConfirmMode] = useState<'purchase' | 'reject' | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<ConfirmTarget | null>(null);
  // 購入を実行する当人。横画面の4人表示では「今アクティブなユーザー」が
  // 存在しないため、どのパネルの操作かをここで明示的に持つ(承認/却下は別途「親」固定で扱う)。
  const [confirmUser, setConfirmUser] = useState<User | null>(null);

  // 結果表示用
  const [levelUpInfo, setLevelUpInfo] = useState<LevelUpInfo | null>(null);
  const [messageData, setMessageData] = useState<{ title: string, text: string, type?: 'success' | 'error', icon?: string } | null>(null);

  // アバターアップロード対象(nullなら非表示)
  const [avatarUser, setAvatarUser] = useState<User | null>(null);

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

  // ★要件9: クエストの完了・取り消しは確認ダイアログを挟まないワンタップ操作。
  // 完了時、要件8のメダル演出(res.earnedMedalsを見て効果音・お祝い表示を出す)もここで行う。
  const runQuestAction = async (user: User, mode: 'complete' | 'cancel', target: Quest | QuestHistory) => {
    const res: ActionResult = mode === 'complete'
      ? await completeQuest(user, target as Quest)
      : await cancelQuest(user, target as QuestHistory);

    if (res.success) {
      if (mode === 'complete') {
        if (res.status === 'pending') {
          setMessageData({ title: "申請完了", text: res.message || "親の承認待ちになりました", type: "success" });
        } else if ((res.earnedMedals ?? 0) > 0) {
          // ★バグ修正(要件8): サーバーは正しくメダルを付与していたが、以前はフロントが
          // res.earnedMedals を一切参照しておらず無反応だった。leveledUpと同様に扱う。
          play('medal');
          setMessageData({ title: "ちいさなメダル獲得！", text: `ちいさなメダルを ${res.earnedMedals} 枚手に入れた！`, type: "success", icon: "🏅" });
        }
      }
      return;
    }

    setMessageData({ title: "エラー", text: resolveErrorText(res, "失敗しました"), type: "error" });
    play('cancel');
  };

  const handleQuestClick = (user: User, q: Quest | QuestHistory, isHistory: boolean) => {
    play('select');

    // 1. 履歴タブなど、明示的に履歴として渡された場合 → ワンタップで取り消し
    if (isHistory) {
      runQuestAction(user, 'cancel', q);
      return;
    }

    // 2. クエストリストから渡された場合 (q は Quest 型)
    // ロック/申請中/完了の判定は useQuestStatus と共通の getQuestLockState に集約
    const { isInfinite, pendingEntry, completedEntry } =
      getQuestLockState(q as Quest, user, completedQuests, pendingQuests);

    // 無限クエストは常に「完了」扱い
    if (isInfinite) {
      runQuestAction(user, 'complete', q);
      return;
    }

    // 3. 完了済み、または申請中リストにあるかを探す
    const historyEntry = pendingEntry || completedEntry;

    if (historyEntry) {
      // 既に履歴がある（完了or申請中）ならワンタップで取り消し。
      // targetには Quest オブジェクトではなく、見つかった History オブジェクトを渡す
      // ※Historyオブジェクトに quest_title が結合されている前提ですが、
      //  もし不足している場合は q.title を補完する必要があります。
      runQuestAction(user, 'cancel', { ...historyEntry, quest_title: ('title' in q ? q.title : undefined) || historyEntry.quest_title });
    } else {
      // 未実施ならワンタップで完了
      runQuestAction(user, 'complete', q);
    }
  };

  const handleBuyReward = (user: User, r: Reward) => {
    setConfirmUser(user);
    setConfirmTarget(r);
    setConfirmMode('purchase');
    play('select');
  };

  // --- Confirm Execution (購入・却下のみ。誤操作の影響が大きいため確認を維持する: 要件9) ---
  const executeConfirm = async () => {
    if (!confirmMode || !confirmTarget) return;
    const actingUser = confirmUser || currentUser;

    let res: ActionResult = { success: false };

    if (confirmMode === 'purchase') {
      res = await buyReward(actingUser, confirmTarget as Reward);
      if (res.success) {
        setMessageData({ title: "購入完了", text: "アイテムを「もちもの」に入れました！", type: "success" });
        // ★要件8: medalサウンドは「メダル獲得時」専用に戻す(以前は購入時にも誤って鳴っていた)
        play('clear');
      }
    } else if (confirmMode === 'reject') {
      // 却下の記録名義は「親」で固定する(要件5)
      res = await rejectQuest(getRepresentativeParent(users), confirmTarget as QuestHistory);
      if (res.success) {
        play('cancel');
      } else {
        // ★却下専用のエラー文言（元の handleReject の window.confirm/reasons ロジックを踏襲）
        const text = resolveErrorText(res, "却下に失敗しました");
        setMessageData({ title: "エラー", text, type: "error" });
        play('cancel');
        setConfirmMode(null);
        setConfirmTarget(null);
        setConfirmUser(null);
        return;
      }
    }

    if (!res.success) {
      const text = resolveErrorText(res, "失敗しました");
      setMessageData({ title: "エラー", text, type: "error" });
      play('cancel');
    }

    setConfirmMode(null);
    setConfirmTarget(null);
    setConfirmUser(null);
  };

  // 承認ハンドラ: 記録名義は「親」で固定する(要件5)
  const handleApprove = async (history: QuestHistory) => {
    const res = await approveQuest(getRepresentativeParent(users), history);
    if (res.success) {
      play('approve');
    } else {
      setMessageData({ title: "エラー", text: resolveErrorText(res, "承認に失敗しました"), type: "error" });
      play('cancel');
    }
  };

  const handleReject = (history: QuestHistory) => {
    setConfirmTarget(history);
    setConfirmMode('reject');
    setConfirmUser(null); // reject は getRepresentativeParent で親を確定するため不要
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
        hideUserSwitcher={layoutMode === 'landscape'}
      />

      {/* ★修正①: max-w-md (スマホ幅) 固定を廃止し、md以上で幅広にする。
          横画面(4人表示)では画面幅をフルに使う */}

      <div className={`p-4 space-y-4 w-full mx-auto transition-all duration-300 ${layoutMode === 'landscape' ? 'max-w-7xl' : 'max-w-md md:max-w-5xl'}`}>

        {viewMode === 'main' && layoutMode === 'landscape' && (
          <FamilyDashboard
            users={users}
            quests={quests}
            completedQuests={completedQuests}
            pendingQuests={pendingQuests}
            rewards={rewards}
            pendingInventory={pendingInventory}
            onQuestClick={(user, q) => handleQuestClick(user, q, false)}
            onBuyReward={handleBuyReward}
            onApprove={handleApprove}
            onReject={handleReject}
            onAvatarClick={(user) => setAvatarUser(user)}
          />
        )}

        {viewMode === 'main' && layoutMode === 'portrait' && (
          <>
            <UserStatusCard
              user={currentUser}
              onAvatarClick={() => setAvatarUser(currentUser)}
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
                  onQuestClick={(q) => handleQuestClick(currentUser, q, false)}
                />
              )}

              {activeTab === 'shop' && (
                <div className="animate-slide-in-right">
                  <RewardShop
                    rewards={rewards}
                    currentUser={currentUser}
                    onBuy={(r) => handleBuyReward(currentUser, r)}
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
          icon={messageData.icon}
          onClose={() => setMessageData(null)}
        />
      )}

      {avatarUser && (
        <AvatarUploader
          user={avatarUser}
          onClose={() => setAvatarUser(null)}
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