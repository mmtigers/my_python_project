import { useState, useRef, useEffect, lazy, Suspense } from 'react';
import { motion } from 'framer-motion';
import { WifiOff, AlertTriangle } from 'lucide-react';
import { INITIAL_USERS } from './lib/masterData';
import { useGameData, LevelUpInfo } from './hooks/useGameData';
import { useSound } from './hooks/useSound';
import { useLayoutMode } from './hooks/useLayoutMode';
import { useOnlineStatus } from './hooks/useOnlineStatus';
import { useCurrentUser } from './hooks/useCurrentUser';
import { useConfirmDialog } from './hooks/useConfirmDialog';
import { useSettings } from './context/useSettings';
import { useToast } from './context/useToast';
import RewardShop from './features/shop/components/RewardShop';
import { InventoryList } from './features/shop/components/InventoryList';
import FamilyDashboard from './features/family/components/FamilyDashboard';

import { CompletedSignal, ID, Quest, QuestHistory, Reward, User } from '@/types';
import { getQuestLockState, getQuestProcessingKey } from './features/quest/hooks/useQuestStatus';
import { isParentUser, getRepresentativeParent } from './lib/userRole';
import { ActionResult, resolveErrorText } from './lib/actionResult';

// UI Components
import Header from './components/layout/Header';
import BottomNav, { BottomNavTab } from './components/layout/BottomNav';
import MessageModal from './components/ui/MessageModal';
import { ConfirmModal } from './components/ui/ConfirmModal';
import ChunkErrorBoundary from './components/ui/ChunkErrorBoundary';

// 初期表示には不要なモーダル類は動的importで分離し、初回バンドルを軽くする
// (実際に開かれるまでチャンクを読み込まない)
const AvatarUploader = lazy(() => import('./components/ui/AvatarUploader'));
const SettingsModal = lazy(() => import('./components/ui/SettingsModal'));

import UserStatusCard from './features/family/components/UserStatusCard';
import QuestList from './features/quest/components/QuestList';
import ApprovalList from './features/quest/components/ApprovalList';
import FamilyLog from './features/family/components/FamilyLog';

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
  const {
    confirmMode, confirmTarget, confirmUser, rejectReason, setRejectReason,
    isConfirming, setIsConfirming, isConfirmingRef,
    openConfirm, closeConfirm,
  } = useConfirmDialog();

  // #119: 承認待ちカードは「スワイプ承認」と「承認ボタン」が併存し、確認モーダルを
  // 挟まず即座にAPIを叩くため、#101のisConfirmingRefと同じ連打対策が無かった。
  // 連打・スワイプ+ボタンのほぼ同時操作で同一履歴に2回目の承認POSTが飛ぶと、
  // サーバー側は1回目で既に承認済みのため400(「承認待ちではありません」)を返し、
  // 実際は成功しているのに「承認に失敗しました」というエラーモーダルが出てしまっていた。
  // 承認は複数のクエストを並行して処理できる必要があるため、単一のbooleanではなく
  // 処理中の履歴idの集合で個別に多重送信を防ぐ。
  const approvingHistoryIdsRef = useRef<Set<ID>>(new Set());
  const isApprovingAllRef = useRef(false);
  // #391(F-L8): 承認ボタンの isLoading 表示用に approvingHistoryIdsRef を state にも写す。
  // 判定は同期的なrefで行い、見た目だけ state に追従させる(#101 と同じ二重化パターン)。
  const [approvingHistoryIds, setApprovingHistoryIds] = useState<ID[]>([]);
  const [isApprovingAll, setIsApprovingAll] = useState(false);
  const syncApprovingHistoryIds = () => setApprovingHistoryIds([...approvingHistoryIdsRef.current]);

  // #391: クエスト完了/取消APIが送信中の (user_id, quest_id) の集合。以前は確認モーダルを
  // 閉じてから await runQuestAction していたため、応答が返るまでカードは未完了のまま
  // 再タップでき、2回目の確認モーダルが1回目の完了後も開いたまま残って「はい」を押すと
  // 400「本日は完了済み」/429 のエラーモーダルになっていた。
  // handleQuestClick で無視し、QuestItem にローディング表示を出すために state にも写す。
  const processingQuestKeysRef = useRef<Set<string>>(new Set());
  const [processingQuestKeys, setProcessingQuestKeys] = useState<string[]>([]);
  const syncProcessingQuestKeys = () => setProcessingQuestKeys([...processingQuestKeysRef.current]);

  // #102: クエスト完了の効果音・無限クエストの連打防止クールダウンは、以前は
  // QuestList側でタップ即時(=確認モーダルを開く前)に発火していたため、確認モーダルで
  // 「キャンセル」しても完了音が鳴り、無限クエストは60秒間タップ不能になっていた。
  // 実際に完了APIが成功した時点でのみ発火させるため、対象クエストのidと発火のたびに
  // 変わるnonceをApp側からQuestList/QuestItemへ通知する。
  // #363: 横画面の4人パネルでは同じsignalが全員のQuestItemに届くため、「誰の完了か」
  // (userId)も載せ、QuestItem側で自分のパネルの完了だけに反応させる。
  const [completedSignal, setCompletedSignal] = useState<CompletedSignal | null>(null);

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
    isLoading,
    gameDataError, refetchGameData,
    completeQuest, approveQuest, rejectQuest, cancelQuest, buyReward,
    refreshData,
  } = useGameData(currentUserIdx, handleLevelUp);

  const currentUser = users[currentUserIdx] || INITIAL_USERS[0];

  // #393: usersが実データに揃ったら保存済みuser_idを解決し、以後のcurrentUserIdxの
  // 変化(ユーザー切替)を都度localStorageへ保存する(#552でuseCurrentUserへ抽出)。
  useCurrentUser(users, currentUserIdx, setCurrentUserIdx);

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
    // #391: 同一ユーザー・同一クエストの完了/取消が送信中なら二重送信しない
    // (再試行(onRetry)から再入する場合は finally で解除済みなので通る)。
    const processingKey = getQuestProcessingKey(user.user_id, target.quest_id);
    if (processingQuestKeysRef.current.has(processingKey)) return;
    processingQuestKeysRef.current.add(processingKey);
    syncProcessingQuestKeys();
    try {
      await runQuestActionInner(user, mode, target);
    } finally {
      processingQuestKeysRef.current.delete(processingKey);
      syncProcessingQuestKeys();
    }
  };

  const runQuestActionInner = async (user: User, mode: 'complete' | 'cancel', target: Quest | QuestHistory) => {
    const res: ActionResult = mode === 'complete'
      ? await completeQuest(user, target as Quest)
      : await cancelQuest(user, target as QuestHistory);

    if (res.success) {
      if (mode === 'complete') {
        const completedQuest = target as Quest;
        // #102: 完了音・無限クエストのクールダウンは、確認モーダルでの「はい」タップ
        // 時点ではなく、実際に完了APIが成功したこの時点で発火させる(以前はQuestList側で
        // タップ即時に鳴らしていたため、モーダルを「キャンセル」しても完了音が鳴り、
        // 無限クエストはクールダウンに入ってしまっていた)。
        // 子ども(role_child)の完了報告は親の承認待ち(status: 'pending')になるのが常だが、
        // それでも「提出」自体は完了しているため、鳴らす対象・クールダウン対象から
        // pending を除外しない(除外すると子どもに対しては常に無音・無クールダウンになり、
        // 無限クエストを連打で何度も申請できてしまう)。
        play(completedQuest.quest_type === 'daily' || completedQuest._isInfinite ? 'clear' : 'submit');
        const idForSignal = completedQuest.quest_id;
        if (idForSignal !== undefined) {
          setCompletedSignal({ id: idForSignal, userId: user.user_id, nonce: Date.now() });
        }
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

  // #530: 以前の第3引数 isHistory(履歴タブからのワンタップ取消)は全呼び出しが false で
  // 到達不能な分岐だったため削除した。本関数はクエストリストからのタップ/長押し専用。
  const handleQuestClick = (user: User, q: Quest) => {
    // #391: 送信中のクエストの再タップは静かに無視する(確認モーダルも開かない)。
    if (processingQuestKeysRef.current.has(getQuestProcessingKey(user.user_id, q.quest_id))) return;
    play('select');

    // クエストリストから渡された場合 (q は Quest 型)
    // ロック/申請中/完了の判定は useQuestStatus と共通の getQuestLockState に集約
    const { isInfinite, pendingEntry, completedEntry } =
      getQuestLockState(q, user, completedQuests, pendingQuests);

    // 無限クエストは常に「完了」扱い
    // ★実機検証で子どもの誤操作(意図しない完了)が多かったため、完了(クリア)には
    // 確認ダイアログを挟む(取り消しは長押しで保護されているため対象外)。
    // ただし子どもの申請が承認待ち(pendingEntry)のときは、カード側で長押し(取消)のみが
    // 有効になっているため、ここでも通常クエストと同じ取消経路へ流す。以前は pendingEntry を
    // 見る前に無条件で完了モーダルを開いていたため、申請中の無限クエストは取り消せず、
    // 完了しようとしても「すでに申請中です」のエラーになる袋小路だった。
    if (isInfinite && !pendingEntry) {
      openConfirm('complete', q, user);
      return;
    }

    // 3. 完了済み、または申請中リストにあるかを探す
    // (無限クエストは完了済み履歴(completedEntry)を取消対象にしない: 周回前提のため)
    const historyEntry = isInfinite ? pendingEntry : (pendingEntry || completedEntry);

    if (historyEntry) {
      // 既に履歴がある（完了or申請中）ならワンタップで取り消し。
      // targetには Quest オブジェクトではなく、見つかった History オブジェクトを渡す
      // ※Historyオブジェクトに quest_title が結合されている前提ですが、
      //  もし不足している場合は q.title を補完する必要があります。
      runQuestAction(user, 'cancel', { ...historyEntry, quest_title: q.title || historyEntry.quest_title });
    } else {
      // 未実施なら確認ダイアログを挟んでから完了
      openConfirm('complete', q, user);
    }
  };

  const handleBuyReward = (user: User, r: Reward) => {
    openConfirm('purchase', r, user);
    play('select');
  };

  // --- Confirm Execution (完了・購入・却下。子どもの誤操作対策として確認を挟む) ---
  const executeConfirm = async () => {
    if (!confirmMode || !confirmTarget) return;
    // #101: 「はい」の連打で、1回目のレスポンス前に2回目の実行が発火するのを防ぐ。
    // (サーバー側にもスパムチェック/ロックを追加済みだが、フロント側でも連打そのものを
    // 抑止し、連打の2回目がエラートーストになるのを防ぐ)
    if (isConfirmingRef.current) return;
    isConfirmingRef.current = true;
    setIsConfirming(true);

    try {
      const actingUser = confirmUser || currentUser;

      if (confirmMode === 'complete') {
        // 完了処理そのもの(メダル演出・エラー表示含む)はrunQuestActionに委ねる。
        // モーダルは先に閉じ、成功/失敗の通知はトースト/エラーモーダル側で行う。
        const target = confirmTarget as Quest;
        closeConfirm();
        await runQuestAction(actingUser, 'complete', target);
        return;
      }

      let res: ActionResult = { success: false };

      if (confirmMode === 'purchase') {
        // #245: actingUser(confirmUser)はモーダルを開いた時点のスナップショットであり、
        // 背景ポーリング(useGameDataの10秒間隔)によるゴールド残高の更新に追従しない。
        // buyReward内のローカル事前チェック((user.gold || 0) < cost)がこの古い残高で
        // 判定してしまうと、実際には購入可能な状況でも誤って「お金が足りません」と
        // なりAPIコール自体がブロックされる。実行直前にusersから同一user_idの最新
        // オブジェクトを引き直し、鮮度の高い残高でチェック・購入を行う。
        const freshActingUser = users.find(u => u.user_id === actingUser.user_id) || actingUser;
        res = await buyReward(freshActingUser, confirmTarget as Reward);
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

      closeConfirm();
    } finally {
      isConfirmingRef.current = false;
      setIsConfirming(false);
    }
  };

  // 承認ハンドラ: 記録名義は「親」で固定する(要件5)
  const handleApprove = async (history: QuestHistory) => {
    // #119: 同一履歴への多重送信は静かに無視する(2回目のタップ・スワイプは
    // 1回分として扱い、エラー表示を出さない)。
    // #391(F-L8): 一括承認中は対象の全idが先に集合へ入るため、一括承認中の個別タップも
    // ここで同様に無視される(以前は一括承認と競合して400のエラーモーダルになっていた)。
    if (history.id != null) {
      if (approvingHistoryIdsRef.current.has(history.id)) return;
      approvingHistoryIdsRef.current.add(history.id);
      syncApprovingHistoryIds();
    }
    try {
      const res = await approveQuest(getRepresentativeParent(users), history);
      if (res.success) {
        play('approve');
        // ★バグ修正(M-6-1): 承認APIのearnedMedalsを見て、完了フロー(runQuestAction)と
        // 同様にメダル獲得演出を出す(以前は承認経由だと一切反映されなかった)。
        // ★バグ修正(Issue #238): 兄妹連携クエストのカスケード承認では相方
        // (自分でタップしなかった方の子ども)側もメダルを獲得しうるため、
        // partnerEarnedMedalsも合算して演出に反映する。
        const totalEarnedMedals = (res.earnedMedals ?? 0) + (res.partnerEarnedMedals ?? 0);
        if (totalEarnedMedals > 0) {
          play('medal');
          showToast({ title: "ちいさなメダル獲得！", text: `ちいさなメダルを ${totalEarnedMedals} 枚手に入れた！`, icon: "🏅" });
        }
      } else {
        setMessageData({
          title: "エラー",
          text: resolveErrorText(res, "承認に失敗しました"),
          onRetry: () => handleApprove(history),
        });
        play('cancel');
      }
    } finally {
      if (history.id != null) {
        approvingHistoryIdsRef.current.delete(history.id);
        syncApprovingHistoryIds();
      }
    }
  };

  // 角度⑩: 承認待ちが複数あるとき、1件ずつ承認する手間を減らす一括承認
  const handleApproveAll = async () => {
    // #119: 一括承認ボタンの連打で、1回目のループが終わる前に2回目が
    // 同じ履歴を並行して承認しようとし400になるのを防ぐ。
    if (isApprovingAllRef.current) return;
    isApprovingAllRef.current = true;
    setIsApprovingAll(true);
    // #391(F-L8): 個別承認が送信中の履歴は一括の対象から外し(応答待ちの行を二重に
    // 承認して400にしない)、残りの全idを先に approvingHistoryIdsRef へ入れて、
    // 一括処理中の個別タップを handleApprove 側で無視させる。
    const claimedIds: ID[] = [];
    try {
      // ★バグ修正(M-6-2): 古いpendingQuestsクロージャではなく、refで常に最新の
      // 一覧を参照する(このハンドラ自体が古いonRetryとして再試行されても正しく動く)。
      const targets = [...pendingQuestsRef.current].filter(h =>
        h.id == null || !approvingHistoryIdsRef.current.has(h.id)
      );
      if (targets.length === 0) return;
      for (const h of targets) {
        if (h.id != null) {
          approvingHistoryIdsRef.current.add(h.id);
          claimedIds.push(h.id);
        }
      }
      syncApprovingHistoryIds();

      let successCount = 0;
      let totalEarnedMedals = 0;
      // 兄妹連携クエストは片方を承認するとサーバー側で相方の行も自動承認される。
      // 相方のidをここに記録し、後続ループで個別に承認APIを叩いて400にならないようにする。
      const cascadedIds = new Set<ID>();
      for (const history of targets) {
        if (history.id != null && cascadedIds.has(history.id)) {
          successCount++;
          continue;
        }
        const res = await approveQuest(getRepresentativeParent(users), history);
        if (res.success) {
          successCount++;
          // #238: 兄妹連携クエストのカスケード承認では相方側もメダルを獲得しうる
          totalEarnedMedals += (res.earnedMedals ?? 0) + (res.partnerEarnedMedals ?? 0);
          if (history.linked_history_id != null) {
            cascadedIds.add(history.linked_history_id);
          }
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
    } finally {
      for (const id of claimedIds) approvingHistoryIdsRef.current.delete(id);
      syncApprovingHistoryIds();
      isApprovingAllRef.current = false;
      setIsApprovingAll(false);
    }
  };

  const handleReject = (history: QuestHistory) => {
    // reject は getRepresentativeParent で親を確定するため confirmUser は不要
    openConfirm('reject', history);
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

      {/* #390: /api/quest/data の取得失敗(ネットワーク・Zod検証失敗)を画面に出す。
          以前はブラウザの console でしか分からず、全端末が「サーバーに繋がりません」の
          フォールバック表示または最後に成功した古いデータのまま無言になっていた。
          オフラインバナーとは別物(オンラインでもサーバー側の応答不整合で起きる)。 */}
      {gameDataError && (
        <div
          role="alert"
          className={`fixed inset-x-0 z-40 bg-amber-700 text-white text-xs font-bold px-3 py-1.5 flex items-center justify-center gap-2 ${isOnline ? 'top-0' : 'top-7'}`}
        >
          <AlertTriangle size={14} className="shrink-0" />
          <span className="truncate">データの取得に失敗しました: {gameDataError}</span>
          <button
            type="button"
            onClick={() => { refetchGameData(); play('tap'); }}
            className="shrink-0 rounded bg-amber-900 px-2 py-0.5 border border-amber-400 hover:bg-amber-800 transition-colors"
          >
            再試行
          </button>
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
        showUserSwitcher={layoutMode !== 'landscape'}
        showLogSwitcher={layoutMode !== 'portrait'}
        showBackToMain={layoutMode === 'landscape'}
        onBackToMain={() => { setViewMode('main'); play('tap'); }}
      />

      {/* ★修正①: max-w-md (スマホ幅) 固定を廃止し、md以上で幅広にする。
          横画面(4人表示)では画面幅をフルに使う */}

      {/* ★修正②: 横画面のコンテナ幅を固定px(max-w-7xl=1280px)から画面幅比率(vw)ベースに変更。
          Echo Show 15(1280px)相当ではほぼ従来通りの見え方を維持しつつ、より横幅の広い画面
          (PCブラウザ等)では左右の余白が画面幅に対して一定割合(約4%ずつ)に収まるようにし、
          各プレイヤーパネルの表示幅を広げる。超ワイド画面での間延びを防ぐため上限も併設する。 */}
      <div className={`${densityWrapperClass} w-full mx-auto transition-all duration-300 ${layoutMode === 'landscape' ? 'max-w-[min(92vw,1800px)]' : 'max-w-md md:max-w-5xl'}`}>

        {viewMode === 'main' && layoutMode === 'landscape' && (
          <FamilyDashboard
            users={users}
            quests={quests}
            completedQuests={completedQuests}
            pendingQuests={pendingQuests}
            rewards={rewards}
            onQuestClick={handleQuestClick}
            onBuyReward={handleBuyReward}
            onApprove={handleApprove}
            onReject={handleReject}
            onApproveAll={handleApproveAll}
            completedSignal={completedSignal}
            processingQuestKeys={processingQuestKeys}
            busyHistoryIds={approvingHistoryIds}
            isApprovingAll={isApprovingAll}
            onAvatarClick={(user) => setAvatarUser(user)}
          />
        )}

        {viewMode === 'main' && layoutMode === 'portrait' && (
          <>
            {/* 角度⑰: ステータスカードを左右スワイプすると、ヘッダーのアバターをタップした時と
                同様にプレイヤー(家族)を切り替えられるようにする。末尾/先頭では折り返さない
                (タブ切替スワイプと同じ挙動に揃える)。 */}
            <motion.div
              onPanEnd={(_e, info) => {
                if (info.offset.x < -60 && currentUserIdx < users.length - 1) handleUserChange(currentUserIdx + 1);
                else if (info.offset.x > 60 && currentUserIdx > 0) handleUserChange(currentUserIdx - 1);
              }}
            >
              <UserStatusCard
                user={currentUser}
                onAvatarClick={() => setAvatarUser(currentUser)}
              />
            </motion.div>

            {isParentUser(currentUser) && (
              <ApprovalList
                pendingQuests={pendingQuests}
                users={users}
                onApprove={handleApprove}
                onReject={handleReject}
                onApproveAll={handleApproveAll}
                busyHistoryIds={approvingHistoryIds}
                isApprovingAll={isApprovingAll}
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
                  onQuestClick={(q) => handleQuestClick(currentUser, q)}
                  completedSignal={completedSignal}
                  processingQuestKeys={processingQuestKeys}
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
        onCancel={() => { closeConfirm(); play('cancel'); }}
        isConfirming={isConfirming}
      />

      {messageData && (
        <MessageModal
          title={messageData.title}
          message={messageData.text}
          onRetry={messageData.onRetry}
          onClose={() => setMessageData(null)}
        />
      )}

      {/* #362: SW更新で旧チャンクがprecacheから消えた後に lazy() の import() が404すると、
          ErrorBoundaryが無い場合はルートごとアンマウントされ白画面になる。
          ChunkErrorBoundaryがチャンク読込失敗を検知して自動で再読み込みする。 */}
      <ChunkErrorBoundary>
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
      </ChunkErrorBoundary>

    </div>
  );
}

export default App;
