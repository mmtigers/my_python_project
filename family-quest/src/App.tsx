import { useState, useRef, useEffect, lazy, Suspense } from 'react';
import { motion } from 'framer-motion';
import { WifiOff } from 'lucide-react';
import { INITIAL_USERS } from './lib/masterData';
import { useGameData, LevelUpInfo } from './hooks/useGameData';
import { useSound } from './hooks/useSound';
import { useLayoutMode } from './hooks/useLayoutMode';
import { useOnlineStatus } from './hooks/useOnlineStatus';
import { useSettings } from './context/useSettings';
import { useToast } from './context/useToast';
import RewardShop from './features/shop/components/RewardShop';
import { InventoryList } from './features/shop/components/InventoryList';
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

// 却下理由のプリセット。自由入力の手間を省き、あとで見返した時にも理由がわかるようにする。
const REJECT_REASONS = ['写真が不明瞭', 'まだ終わっていない', '重複している', 'その他'];

// UI Components
import Header from './components/layout/Header';
import BottomNav, { BottomNavTab } from './components/layout/BottomNav';
import MessageModal from './components/ui/MessageModal';
import { Button } from './components/ui/Button';
import { Modal } from './components/ui/Modal';

// 初期表示には不要なモーダル類は動的importで分離し、初回バンドルを軽くする
// (実際に開かれるまでチャンクを読み込まない)
const AvatarUploader = lazy(() => import('./components/ui/AvatarUploader'));
const SettingsModal = lazy(() => import('./components/ui/SettingsModal'));

import UserStatusCard from './features/family/components/UserStatusCard';
import QuestList from './features/quest/components/QuestList';
import ApprovalList from './features/quest/components/ApprovalList';
import FamilyLog from './features/family/components/FamilyLog';

// ConfirmModal の target に渡りうる型。モードごとに実際に持っているプロパティが異なるため、
// メッセージ生成はモードごとに個別にキャストして組み立てる（getMessage 内）。
// ★実機検証で子どもの誤操作が多かったため、クエスト完了(クリア)には確認ダイアログを復活させた。
// 取り消しは長押しでのみ発火する(QuestList側のuseLongPress)ため、引き続き確認なしのワンタップとする。
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
  mode, target, rejectReason, onSelectRejectReason, onConfirm, onCancel
}: {
  mode: 'complete' | 'purchase' | 'reject' | null,
  target: ConfirmTarget | null,
  rejectReason: string | null,
  onSelectRejectReason: (reason: string) => void,
  onConfirm: () => void,
  onCancel: () => void
}) => {
  if (!mode || !target) return null;

  const getMessage = (): { title: string; text: string } => {
    switch (mode) {
      case 'complete': {
        const t = target as Quest;
        return { title: 'クエスト完了', text: `「${t.title}」を完了にしますか？` };
      }
      case 'purchase': {
        const t = target as Reward;
        // Lowバグ修正: masterData.jsのフォールバック報酬はcost_goldを持たず
        // costのみのため、cost_gold単独参照だと「undefinedG」表示になっていた。
        return { title: 'アイテム購入', text: `「${t.title}」を ${t.cost_gold ?? t.cost}G で買いますか？` };
      }
      case 'reject':
        return { title: '却下確認', text: '本当に却下しますか？' };
    }
  };
  const msg = getMessage();

  return (
    <Modal isOpen={true} onClose={onCancel} title={msg.title}>
      <div className="p-4">
        <p className="whitespace-pre-wrap text-center mb-4">{msg.text}</p>

        {/* 角度⑫: 却下理由をプリセットからワンタップで選べるようにし、自由入力の手間を省く */}
        {mode === 'reject' && (
          <div className="flex flex-wrap gap-2 justify-center mb-6">
            {REJECT_REASONS.map(r => (
              <button
                key={r}
                onClick={() => onSelectRejectReason(r)}
                className={`min-h-[36px] px-3 py-1.5 rounded-full text-xs font-bold border-2 transition-colors ${rejectReason === r
                  ? 'bg-red-600 border-red-400 text-white'
                  : 'bg-slate-800 border-slate-600 text-slate-300 hover:border-slate-400'
                  }`}
              >
                {r}
              </button>
            ))}
          </div>
        )}

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
  const isOnline = useOnlineStatus();
  const { density, iconFirstUserIds } = useSettings();
  const { showToast } = useToast();

  const [activeTab, setActiveTab] = useState<'quest' | 'shop' | 'inventory'>('quest');
  const [viewMode, setViewMode] = useState<'main' | 'familyLog'>('main');
  const [currentUserIdx, setCurrentUserIdx] = useState(0);

  // モーダル状態 (完了・購入・却下。取消は長押しでのみ発火するため確認を挟まない)
  const [confirmMode, setConfirmMode] = useState<'complete' | 'purchase' | 'reject' | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<ConfirmTarget | null>(null);
  // クエスト完了/購入を実行する当人。横画面の4人表示では「今アクティブなユーザー」が
  // 存在しないため、どのパネルの操作かをここで明示的に持つ(承認/却下は別途「親」固定で扱う)。
  const [confirmUser, setConfirmUser] = useState<User | null>(null);
  const [rejectReason, setRejectReason] = useState<string | null>(null);

  // エラー表示用(成功系の通知はすべてトースト化したため、ここはエラー専用)
  const [messageData, setMessageData] = useState<{ title: string, text: string, onRetry?: () => void } | null>(null);

  // アバターアップロード対象(nullなら非表示)
  const [avatarUser, setAvatarUser] = useState<User | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  // 角度⑤: レベルアップ/メダル獲得などの「成功の演出」は、作業を止めるブロッキングモーダルから
  // 自動で消えるトーストへ変更(連続してクエストを完了する際にテンポが悪かったため)。
  const handleLevelUp = (info: LevelUpInfo) => {
    play('levelUp');
    showToast({ title: 'LEVEL UP!', text: `${info.user}は Lv.${info.level} になった！`, icon: '⚡' });
  };

  const {
    users, quests, rewards, completedQuests, pendingQuests,
    chronicle,
    pendingInventory,
    isLoading,
    completeQuest, approveQuest, rejectQuest, cancelQuest, buyReward,
    refreshData,
  } = useGameData(currentUserIdx, handleLevelUp);

  const currentUser = users[currentUserIdx] || INITIAL_USERS[0];

  // ★バグ修正(M-6-2): handleApproveAllのonRetryが承認失敗時点の古いpendingQuests
  // クロージャを掴んだままになり、再試行すると既に承認済みの項目まで再承認しようとして
  // 400エラーになり続けていた。refで常に最新のpendingQuestsを参照できるようにする。
  const pendingQuestsRef = useRef(pendingQuests);
  useEffect(() => {
    pendingQuestsRef.current = pendingQuests;
  }, [pendingQuests]);

  // --- Handlers ---
  const handleUserChange = (idx: number) => {
    setCurrentUserIdx(idx);
    // ★修正③: ユーザーアイコンを押したら必ずメイン画面(User View)に戻す
    setViewMode('main');
    play('tap');
  };

  // 完了(confirmMode='complete'の確認後)・取り消し(長押しでワンタップ)の実行本体。
  // 完了時、要件8のメダル演出(res.earnedMedalsを見て効果音・お祝い表示を出す)もここで行う。
  const runQuestAction = async (user: User, mode: 'complete' | 'cancel', target: Quest | QuestHistory) => {
    const res: ActionResult = mode === 'complete'
      ? await completeQuest(user, target as Quest)
      : await cancelQuest(user, target as QuestHistory);

    if (res.success) {
      if (mode === 'complete') {
        if (res.status === 'pending') {
          showToast({ title: "申請完了", text: res.message || "親の承認待ちになりました", icon: '📨' });
        } else if ((res.earnedMedals ?? 0) > 0) {
          // ★バグ修正(要件8): サーバーは正しくメダルを付与していたが、以前はフロントが
          // res.earnedMedals を一切参照しておらず無反応だった。leveledUpと同様に扱う。
          play('medal');
          showToast({ title: "ちいさなメダル獲得！", text: `ちいさなメダルを ${res.earnedMedals} 枚手に入れた！`, icon: "🏅" });
        }
      }
      return;
    }

    setMessageData({
      title: "エラー",
      text: resolveErrorText(res, "失敗しました"),
      onRetry: () => runQuestAction(user, mode, target),
    });
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
    // ★実機検証で子どもの誤操作(意図しない完了)が多かったため、完了(クリア)には
    // 確認ダイアログを挟む(取り消しは長押しで保護されているため対象外)。
    if (isInfinite) {
      setConfirmUser(user);
      setConfirmTarget(q);
      setConfirmMode('complete');
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
      // 未実施なら確認ダイアログを挟んでから完了
      setConfirmUser(user);
      setConfirmTarget(q);
      setConfirmMode('complete');
    }
  };

  const handleBuyReward = (user: User, r: Reward) => {
    setConfirmUser(user);
    setConfirmTarget(r);
    setConfirmMode('purchase');
    play('select');
  };

  // --- Confirm Execution (完了・購入・却下。子どもの誤操作対策として確認を挟む) ---
  const executeConfirm = async () => {
    if (!confirmMode || !confirmTarget) return;
    const actingUser = confirmUser || currentUser;

    if (confirmMode === 'complete') {
      // 完了処理そのもの(メダル演出・エラー表示含む)はrunQuestActionに委ねる。
      // モーダルは先に閉じ、成功/失敗の通知はトースト/エラーモーダル側で行う。
      const target = confirmTarget as Quest;
      setConfirmMode(null);
      setConfirmTarget(null);
      setConfirmUser(null);
      await runQuestAction(actingUser, 'complete', target);
      return;
    }

    let res: ActionResult = { success: false };

    if (confirmMode === 'purchase') {
      res = await buyReward(actingUser, confirmTarget as Reward);
      if (res.success) {
        showToast({ title: "購入完了", text: "アイテムを「もちもの」に入れました！", icon: '🛍️' });
        // ★要件8: medalサウンドは「メダル獲得時」専用に戻す(以前は購入時にも誤って鳴っていた)
        play('clear');
      }
    } else if (confirmMode === 'reject') {
      // 却下の記録名義は「親」で固定する(要件5)
      res = await rejectQuest(getRepresentativeParent(users), confirmTarget as QuestHistory, rejectReason || undefined);
      if (res.success) {
        play('cancel');
      }
    }

    if (!res.success) {
      const fallback = confirmMode === 'reject' ? "却下に失敗しました" : "失敗しました";
      setMessageData({ title: "エラー", text: resolveErrorText(res, fallback) });
      play('cancel');
      // ★角度⑨: 確認モーダルは閉じずに残し、エラーを閉じたあとにもう一度「はい」で
      // 再試行できるようにする(状態[購入対象/却下理由]を失わないため)
      return;
    }

    setConfirmMode(null);
    setConfirmTarget(null);
    setConfirmUser(null);
    setRejectReason(null);
  };

  // 承認ハンドラ: 記録名義は「親」で固定する(要件5)
  const handleApprove = async (history: QuestHistory) => {
    const res = await approveQuest(getRepresentativeParent(users), history);
    if (res.success) {
      play('approve');
      // ★バグ修正(M-6-1): 承認APIのearnedMedalsを見て、完了フロー(runQuestAction)と
      // 同様にメダル獲得演出を出す(以前は承認経由だと一切反映されなかった)。
      if ((res.earnedMedals ?? 0) > 0) {
        play('medal');
        showToast({ title: "ちいさなメダル獲得！", text: `ちいさなメダルを ${res.earnedMedals} 枚手に入れた！`, icon: "🏅" });
      }
    } else {
      setMessageData({
        title: "エラー",
        text: resolveErrorText(res, "承認に失敗しました"),
        onRetry: () => handleApprove(history),
      });
      play('cancel');
    }
  };

  // 角度⑩: 承認待ちが複数あるとき、1件ずつ承認する手間を減らす一括承認
  const handleApproveAll = async () => {
    // ★バグ修正(M-6-2): 古いpendingQuestsクロージャではなく、refで常に最新の
    // 一覧を参照する(このハンドラ自体が古いonRetryとして再試行されても正しく動く)。
    const targets = [...pendingQuestsRef.current];
    if (targets.length === 0) return;

    let successCount = 0;
    let totalEarnedMedals = 0;
    for (const history of targets) {
      const res = await approveQuest(getRepresentativeParent(users), history);
      if (res.success) {
        successCount++;
        totalEarnedMedals += res.earnedMedals ?? 0;
      }
    }

    if (successCount > 0) play('approve');
    if (totalEarnedMedals > 0) {
      play('medal');
      showToast({ title: "ちいさなメダル獲得！", text: `ちいさなメダルを ${totalEarnedMedals} 枚手に入れた！`, icon: "🏅" });
    }

    if (successCount === targets.length) {
      showToast({ title: "一括承認", text: `${successCount}件のクエストを承認しました`, icon: '✅' });
    } else {
      setMessageData({
        title: "エラー",
        text: `一部の承認に失敗しました (${successCount}/${targets.length}件成功)`,
        onRetry: () => handleApproveAll(),
      });
      play('cancel');
    }
  };

  const handleReject = (history: QuestHistory) => {
    setConfirmTarget(history);
    setConfirmMode('reject');
    setConfirmUser(null); // reject は getRepresentativeParent で親を確定するため不要
    setRejectReason(null);
    play('select');
  };

  const getHeaderViewMode = () => {
    if (viewMode === 'familyLog') return 'familyLog';
    return 'user';
  };

  // 角度⑦: 縦画面はフッターナビ(クエスト/ごほうび/記録)に一本化する
  const handleBottomNavChange = (tab: BottomNavTab) => {
    play('tap');
    if (tab === 'familyLog') {
      setViewMode('familyLog');
    } else {
      setViewMode('main');
      setActiveTab(tab);
    }
  };

  // 角度⑧: 表示密度設定を反映する余白のクラス
  const densityWrapperClass = density === 'compact' ? 'p-2 space-y-2' : 'p-4 space-y-4';

  if (isLoading) return <div className="p-10 text-center">Loading Family Quest...</div>;

  return (
    <div className="min-h-screen bg-gray-900 pb-20 font-sans text-gray-100">
      {!isOnline && (
        <div className="fixed top-0 inset-x-0 z-40 bg-red-800 text-white text-xs font-bold text-center py-1.5 flex items-center justify-center gap-2">
          <WifiOff size={14} /> オフラインです。最新の情報ではない可能性があります
        </div>
      )}

      {/* ★バグ修正: 横画面で記録(familyLog)表示中、以前はユーザー切替行(4人分のボタン)を
          そのまま出しており「ホームに戻る」という意図が伝わらなかった。
          代わりに単一のホームボタンを表示する。トップ画面でも同じボタンを表示し統一感を持たせる
          (トップ画面では押しても画面遷移は起きない: 既にメイン画面のため) */}
      <Header
        users={users}
        currentUserIdx={currentUserIdx}
        viewMode={getHeaderViewMode()}
        onUserSwitch={handleUserChange}
        onLogSwitch={() => { setViewMode('familyLog'); play('select'); }}
        onSettingsClick={() => { setSettingsOpen(true); play('tap'); }}
        hideUserSwitcher={layoutMode === 'landscape'}
        hideLogSwitcher={layoutMode === 'portrait'}
        showBackToMain={layoutMode === 'landscape'}
        onBackToMain={() => { setViewMode('main'); play('tap'); }}
      />

      {/* ★修正①: max-w-md (スマホ幅) 固定を廃止し、md以上で幅広にする。
          横画面(4人表示)では画面幅をフルに使う */}

      <div className={`${densityWrapperClass} w-full mx-auto transition-all duration-300 ${layoutMode === 'landscape' ? 'max-w-7xl' : 'max-w-md md:max-w-5xl'}`}>

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
            onApproveAll={handleApproveAll}
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
                onApproveAll={handleApproveAll}
              />
            )}

            {/* 角度⑯: 左右スワイプでもクエスト/ごほうびタブを切り替えられるようにする */}
            <motion.div
              className="min-h-[300px] animate-fade-in"
              onPanEnd={(_e, info) => {
                const order: Array<'quest' | 'shop' | 'inventory'> = ['quest', 'shop', 'inventory'];
                const idx = order.indexOf(activeTab);
                if (info.offset.x < -60 && idx < order.length - 1) setActiveTab(order[idx + 1]);
                else if (info.offset.x > 60 && idx > 0) setActiveTab(order[idx - 1]);
              }}
            >
              {activeTab === 'quest' && (
                <QuestList
                  quests={quests}
                  completedQuests={completedQuests}
                  pendingQuests={pendingQuests}
                  currentUser={currentUser}
                  onQuestClick={(q) => handleQuestClick(currentUser, q, false)}
                  iconFirst={iconFirstUserIds.includes(currentUser.user_id)}
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

              {activeTab === 'inventory' && (
                <div className="animate-slide-in-right">
                  <InventoryList userId={currentUser.user_id} />
                </div>
              )}
            </motion.div>
          </>
        )}

        {viewMode === 'familyLog' && (
          <FamilyLog chronicle={chronicle} users={users} />
        )}

      </div>

      {layoutMode === 'portrait' && (
        <BottomNav
          active={viewMode === 'familyLog' ? 'familyLog' : activeTab}
          onChange={handleBottomNavChange}
        />
      )}

      <ConfirmModal
        mode={confirmMode}
        target={confirmTarget}
        rejectReason={rejectReason}
        onSelectRejectReason={setRejectReason}
        onConfirm={executeConfirm}
        onCancel={() => { setConfirmMode(null); setRejectReason(null); play('cancel'); }}
      />

      {messageData && (
        <MessageModal
          title={messageData.title}
          message={messageData.text}
          onRetry={messageData.onRetry}
          onClose={() => setMessageData(null)}
        />
      )}

      <Suspense fallback={null}>
        {avatarUser && (
          <AvatarUploader
            user={avatarUser}
            onClose={() => setAvatarUser(null)}
            onUploadComplete={() => {
              refreshData();
              showToast({ title: "変更完了", text: "アバターを変更しました！", icon: '🖼️' });
            }}
          />
        )}

        {settingsOpen && (
          <SettingsModal isOpen={settingsOpen} onClose={() => setSettingsOpen(false)} users={users} />
        )}
      </Suspense>

    </div>
  );
}

export default App;
