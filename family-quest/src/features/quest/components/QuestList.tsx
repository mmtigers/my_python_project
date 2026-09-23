import React, { useEffect, useMemo, useState } from 'react';
import { Undo2, Clock, TrendingUp, Lock, Check, Loader2, ChevronDown } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { CompletedSignal, User, Quest, QuestHistory } from '@/types';
import { CooldownRing } from '@/components/ui/CooldownRing';
import { useQuestStatus, getQuestLockState, getQuestProcessingKey, canCancelQuest } from '../hooks/useQuestStatus';
import { isQuestVisibleToUser } from '@/lib/questTargeting';
import { useSound } from '@/hooks/useSound';
import { useLongPress } from '@/hooks/useLongPress';

interface QuestListProps {
    quests: Quest[];
    completedQuests: QuestHistory[];
    pendingQuests: QuestHistory[];
    currentUser: User;
    onQuestClick: (quest: Quest) => void;
    // #102: 完了APIが実際に成功した時点でのみ、対象クエストの完了音・無限クエストの
    // クールダウンを発火させるための通知(App側で管理)。
    completedSignal: CompletedSignal | null;
    // #391: 完了/取消APIが送信中の (user_id, quest_id) キー集合(App側で管理)。
    // 該当カードはローディング表示になりタップ・長押しを受け付けない。
    processingQuestKeys?: string[];
    // 横画面4人表示のパネル内で使うためのモード。
    // true の場合、ビューポート幅基準の md: ブレークポイント(2カラム化・拡大表示)には
    // 依存せず、狭いパネル幅でも崩れないタップ領域確保済みの単一カラム表示にする。
    panelMode?: boolean;
    // アイコン主体・文字量を絞った表示にするか(非識字年齢の子ども向け)。
    // 説明文を非表示にし、アイコンをより大きく見せる。
    iconFirst?: boolean;
}

// バッジは種類が多く同時に出すと読みづらいため、優先度順に並べて
// 上位2件だけを表示する。優先度が低いものは「+N」でまとめて示す。
interface BadgeCandidate {
    key: string;
    priority: number;
    node: React.ReactNode;
}

const MAX_VISIBLE_BADGES = 2;

// 角度②: 実行可能なクエストが多いと、スポットライト・ライトカードが何枚も並んで
// 何をやればいいか一目でわからなくなる(特に低学年の子ども)。同時にカード表示するのは
// 優先度順(ソート順)で上位何件かに絞り、残りは「もっと見る」の先に回す。
// 申請中(isPending)はこの上限の対象外(常にカード表示のままにする。件数も少なく、
// 本人がまだ気にしている状態のため)。
const ACTIONABLE_CARD_LIMIT = 3;

type QuestVariant = 'default' | 'completed' | 'pending' | 'infinite' | 'timeLimit' | 'random' | 'limited' | 'locked';

// 「きょうのすごろく」(RoutineFlow.tsx)と同じノード+接続線の見た目に合わせた、
// useQuestStatus が返す variant ごとの配色。判定ロジック自体は増やさず、既存の
// variant をそのままキーにする。
const QUEST_THEME: Record<QuestVariant, { node: string; nodeDone: string; seg: string; spotlight: string }> = {
    default: { node: 'bg-gradient-to-br from-blue-300 to-blue-500 text-blue-950 border-blue-300', nodeDone: 'bg-blue-900/40 border-blue-500 text-blue-300', seg: 'bg-blue-500', spotlight: 'bg-gradient-to-br from-blue-900/50 to-blue-900/10 border-blue-500/60' },
    infinite: { node: 'bg-gradient-to-br from-cyan-300 to-cyan-500 text-cyan-950 border-cyan-300', nodeDone: 'bg-cyan-900/40 border-cyan-500 text-cyan-300', seg: 'bg-cyan-500', spotlight: 'bg-gradient-to-br from-cyan-900/50 to-cyan-900/10 border-cyan-400/60 shadow-[0_0_8px_rgba(0,255,255,0.15)]' },
    limited: { node: 'bg-gradient-to-br from-pink-300 to-pink-500 text-pink-950 border-pink-300', nodeDone: 'bg-pink-900/40 border-pink-500 text-pink-300', seg: 'bg-pink-500', spotlight: 'bg-gradient-to-br from-pink-900/50 to-pink-900/10 border-pink-400/60' },
    random: { node: 'bg-gradient-to-br from-purple-300 to-purple-500 text-purple-950 border-purple-300', nodeDone: 'bg-purple-900/40 border-purple-500 text-purple-300', seg: 'bg-purple-500', spotlight: 'bg-gradient-to-br from-purple-900/50 to-purple-900/10 border-purple-400/60' },
    timeLimit: { node: 'bg-gradient-to-br from-orange-300 to-orange-500 text-orange-950 border-orange-300', nodeDone: 'bg-orange-900/40 border-orange-500 text-orange-300', seg: 'bg-orange-500', spotlight: 'bg-gradient-to-br from-orange-900/50 to-red-900/10 border-orange-400/60' },
    pending: { node: 'bg-gradient-to-br from-yellow-300 to-yellow-500 text-yellow-950 border-yellow-300', nodeDone: 'bg-yellow-900/40 border-yellow-500 text-yellow-300', seg: 'bg-yellow-500', spotlight: 'bg-gradient-to-br from-yellow-900/50 to-yellow-900/10 border-yellow-500/60' },
    completed: { node: 'bg-gray-800 border-gray-600 text-gray-500', nodeDone: 'bg-green-900/30 border-green-600 text-green-400', seg: 'bg-green-600', spotlight: '' },
    locked: { node: 'bg-gray-800 border-gray-600 text-gray-500', nodeDone: 'bg-gray-800 border-gray-600 text-gray-500', seg: 'bg-gray-700', spotlight: '' },
};

// 個別のクエストアイテムコンポーネント。RoutineFlow の RoutineStepRow と同じ
// 「ノード(アイコン円)+接続線+コンテンツ」の構造を踏襲するが、RoutineFlow が
// 単一の「今ここ」だけをハイライトするのに対し、クエストは順番を強制しないため
// 「未完了かつ未ロック(申請中含む)」を満たす複数件が同時にハイライト表示される。
const QuestItem: React.FC<{
    quest: Quest;
    completedQuests: QuestHistory[];
    pendingQuests: QuestHistory[];
    currentUser: User;
    onClick: (q: Quest) => void;
    completedSignal: CompletedSignal | null;
    isProcessing?: boolean;
    isLast: boolean;
    // 角度②: 実行可能(isActionable)でも、件数上限(ACTIONABLE_CARD_LIMIT)を超えた分は
    // 「もっと見る」で展開されるまでカードにせず縮小1行のままにする。QuestList側で
    // ソート順に基づいて算出し、個々のQuestItemはこのフラグに従うだけ。
    forceSlim?: boolean;
    panelMode?: boolean;
    iconFirst?: boolean;
}> = ({ quest, completedQuests, pendingQuests, currentUser, onClick, completedSignal, isProcessing = false, isLast, forceSlim = false, panelMode, iconFirst }) => {

    const [isCooldown, setIsCooldown] = useState(false);
    const COOLDOWN_MS = 60000;
    const { play } = useSound();

    const {
        isDone, isPending, isInfinite, isRandom, isTimeLimited, isLimited, isLocked,
        displayTitle, variant
    } = useQuestStatus({ quest, currentUser, completedQuests, pendingQuests });

    // #102: 完了音・クールダウンは、タップ時点(確認モーダルが開く前)ではなく、
    // 完了APIが実際に成功した時点(App側からのcompletedSignal)でのみ発火させる。
    // 以前はタップ即時に鳴らしていたため、確認モーダルで「キャンセル」しても完了音が鳴り、
    // 無限クエストは60秒間タップ不能になっていた。
    // #363: 横画面の4人パネルでは同じ completedSignal が全パネルの同一クエストに届くため、
    // クエストidだけでなく「誰の完了か」(userId)も一致する場合のみクールダウンに入れる。
    // 以前は id しか見ておらず、兄が完了した無限クエストが妹・パパ・ママのパネルでも
    // 60秒 "Wait..." になっていた(サーバー側のクールダウンは (user, quest) 単位)。
    // #567: 上記のuserId一致チェックだけでは2つの誤動作が残っていた。
    // (a) 兄が完了しクールダウン中に妹へユーザー切替すると、userId不一致でeffectは
    //     早期returnするが、既にtrueになっているisCooldownを戻す処理が無く、
    //     切り替え先のパネルが操作不能のまま固着していた。
    // (b) タブ切替等でQuestListが再マウントされると、isCooldown(state)は初期化される
    //     一方でcompletedSignal(props)は古いままのため、id/userId一致だけを見ると
    //     とっくに終わっているはずのクールダウンが丸ごと(60秒)再発火していた。
    // completedSignal.nonceは発火時刻(Date.now())であるため、不一致時は明示的に
    // isCooldownを解除し(a)、一致時も経過時間を差し引いた残り時間のみをロックする(b)。
    const questId = quest.quest_id;
    const currentUserId = currentUser.user_id;
    useEffect(() => {
        if (!isInfinite || !completedSignal) return;
        if (completedSignal.id !== questId || completedSignal.userId !== currentUserId) {
            // react-hooks 7 の set-state-in-effect は「effect 内で同期的に setState すると再描画が
            // 連鎖する」ことを咎めるが、ここはサーバーから届いた完了通知(completedSignal)と
            // 壁時計の経過時間にクールダウン表示を合わせる処理で、effect(= 外部との同期)が本来の
            // 置き場所である。推奨される「描画中に算出する」形へ移すと今度は描画中に Date.now() を
            // 呼ぶことになり purity ルールに触れる。加えてこのクールダウンは #363 や上記(a)(b)で
            // 挙動を何度も直してきた子ども向けの処理なので、書き換えによる退行を避け、挙動は
            // 変えずにこの行だけルールを外す(再描画は1回増えるだけで、表示の正しさには影響しない)。
            // eslint-disable-next-line react-hooks/set-state-in-effect -- 上記の理由で意図的
            setIsCooldown(false);
            return;
        }
        const remainingMs = COOLDOWN_MS - (Date.now() - completedSignal.nonce);
        if (remainingMs <= 0) {
            setIsCooldown(false);
            return;
        }
        setIsCooldown(true);
        const timer = setTimeout(() => setIsCooldown(false), remainingMs);
        return () => clearTimeout(timer);
    }, [completedSignal, isInfinite, questId, currentUserId]);

    // ボーナス計算
    const bonusGold = quest.bonus_gold || 0;
    const bonusExp = quest.bonus_exp || 0;
    const hasBonus = bonusGold > 0 || bonusExp > 0;

    // 合計報酬(ゴールドのみ画面表示する。EXPは表示不要のため計算しない)
    const baseGold = quest.gold_gain || 0;
    const totalGold = baseGold + bonusGold;

    // #412(F-L10): masterData.js のフォールバック(案内専用の疑似クエスト、
    // quest._isFallback)は完了APIを叩けないため、ロック中と同様にタップ・長押しを
    // 無効化する(以前はタップ可能で、完了しようとすると404等のエラーモーダルになっていた)。
    // #530: 以前ここで OR していた共有クエスト判定(is_shared_* 由来)は、バックエンドが
    // 送出しないフィールドに基づく常に false の分岐だったため削除した。
    const isEffectivelyLocked = isLocked || !!quest._isFallback;

    // 完了済み/申請中の取り消しは「長押し」でのみ発火させ、うっかりタップでの
    // 誤取り消しを防ぐ。
    // (無限クエストは完了済み(isDone)にはならないが、子どもの申請中(isPending)は
    // 通常クエストと同様に取り消し可能。判定は useQuestStatus.canCancelQuest に集約)
    const canCancel = canCancelQuest({ isDone, isPending }, isEffectivelyLocked);

    const runComplete = () => {
        // #102: 完了音・クールダウン開始はここでは行わない(上のuseEffect/App側を参照)。
        // ここではあくまで確認モーダルを開く(onClick)のみを行う。
        // #391: 完了/取消APIの送信中(isProcessing)は再タップを受け付けない。
        if (isCooldown || isEffectivelyLocked || isProcessing) return;
        onClick({ ...quest, _isInfinite: !!isInfinite });
    };

    const runCancel = () => {
        if (isEffectivelyLocked || isProcessing) return;
        play('cancel');
        onClick({ ...quest, _isInfinite: !!isInfinite });
    };

    const { isPressing, pressProgress, wasFiredRecently, clearFiredFlag, handlers: longPressHandlers } = useLongPress({
        onLongPress: runCancel,
        disabled: !canCancel || isProcessing,
        thresholdMs: 550,
    });

    // タップ即実行の対象は「まだ完了/申請していない」通常タップのみ。
    // 完了済み・申請中は canCancel 側(長押し)に処理を委ねる。
    const handleTapComplete = () => {
        if (canCancel || isCooldown) return; // 長押し対象/クールダウン中はタップでは何もしない
        // #389: 長押し(取消)が550msで発火 → 取消API → invalidateQueries → 再取得(LAN内で
        // 100〜300ms)が指を離すより先に終わると、同じDOMノードに本ハンドラが付いた状態で
        // pointerup 由来の click が届き、直前に取り消したクエストの完了確認モーダルが
        // 開いてしまう(子どもが「はい」を押せば即再申請)。長押し発火直後の click は無視する。
        if (wasFiredRecently()) {
            // #568: 取消が成立した直後はcanCancelがfalseに変わり、longPressHandlers
            // (onPointerDown等)自体がこの要素から外れる(下記JSX参照)ため、次の
            // pointerdownを待つだけではフラグが二度とリセットされない。ここで
            // 1回抑止に使った時点で明示的に消費し、以降の正当なタップ(完了確認)を
            // 恒久的にブロックしないようにする。
            clearFiredFlag();
            return;
        }
        runComplete();
    };

    const badgeSizeClasses = panelMode ? 'text-[10px]' : 'text-xs';

    // ▼ バッジ候補を優先度付きで作り、上位2件だけを表示する(角度①: バッジ過多の整理)。
    // 「未開放」バッジは、ロック中のクエストがカード(スポットライト・ライト)ではなく
    // 縮小1行(ロックノード+「未開放」テキスト)で表示されるようになったため廃止した
    // (二重表示を避ける)。
    const badgeCandidates: BadgeCandidate[] = [];
    if (isPending) {
        badgeCandidates.push({
            key: 'pending', priority: 2, node: (
                <span key="pending" className={`bg-yellow-500 text-black ${badgeSizeClasses} px-1.5 py-0.5 rounded font-bold animate-pulse flex items-center gap-1`}>
                    <Clock size={10} /> 申請中
                </span>
            )
        });
    }
    if (isLimited && !isDone && !isPending && !isLocked) {
        badgeCandidates.push({
            key: 'limited', priority: 3, node: (
                <span key="limited" className={`bg-red-600 ${badgeSizeClasses} px-1.5 py-0.5 rounded font-bold`}>期間限定</span>
            )
        });
    }
    if (isTimeLimited && !isDone && !isPending && !isLocked) {
        badgeCandidates.push({
            key: 'timeLimited', priority: 4, node: (
                <span key="timeLimited" className={`bg-yellow-500 text-black ${badgeSizeClasses} px-1.5 py-0.5 rounded font-bold animate-pulse flex items-center gap-1`}>
                    ⏰ {quest.start_time}~{quest.end_time}
                </span>
            )
        });
    }
    const sortedBadges = [...badgeCandidates].sort((a, b) => a.priority - b.priority);
    const visibleBadges = sortedBadges.slice(0, MAX_VISIBLE_BADGES);
    const hiddenBadgeCount = sortedBadges.length - visibleBadges.length;

    // 「実行可能」= 未完了かつ未ロック(申請中も含む)。RoutineFlow の status==='current'
    // と違い、複数件が同時にこの状態になりうる。
    const isActionable = !isDone && !isLocked && !forceSlim;
    const theme = QUEST_THEME[variant];

    const nodeSize = panelMode ? 'w-8 h-8' : (isActionable ? 'w-12 h-12' : 'w-10 h-10');
    const nodeIconSize = panelMode ? 14 : (isActionable ? 22 : 18);
    const nodeIconTextSize = panelMode ? 'text-base' : 'text-xl';

    let nodeClass = 'border-gray-600 bg-gray-800 text-gray-500';
    if (isDone) nodeClass = theme.nodeDone;
    else if (isActionable) nodeClass = `${theme.node} ${!panelMode ? 'animate-pulse' : ''}`;

    const interactiveProps = canCancel
        ? {
              // #660: 取消は長押し専用のため、キーボードからも到達できるよう
              // role/tabIndex/Enter・Spaceでの取消操作を明示的に用意する。
              role: 'button' as const,
              tabIndex: isEffectivelyLocked || isProcessing ? -1 : 0,
              'aria-label': `${quest.title} の完了を取り消す`,
              onKeyDown: (e: React.KeyboardEvent) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      runCancel();
                  }
              },
              ...longPressHandlers,
          }
        : {};

    return (
        <div className="flex gap-3">
            {/* ノード + 接続線カラム */}
            <div className="flex flex-col items-center flex-none" style={{ width: panelMode ? 32 : 40 }}>
                <div className={`rounded-full border-2 flex items-center justify-center flex-none transition-all ${nodeClass} ${nodeSize}`}>
                    {isDone ? (
                        <Check size={nodeIconSize} />
                    ) : isLocked ? (
                        <Lock size={nodeIconSize} />
                    ) : (
                        <span className={nodeIconTextSize}>{quest.icon_key}</span>
                    )}
                </div>
                {!isLast && (
                    <div className={`w-[3px] flex-1 min-h-[12px] rounded-full ${isDone ? theme.seg : 'bg-gray-700'}`} />
                )}
            </div>

            {/* コンテンツカラム */}
            <div className={`flex-1 min-w-0 ${panelMode ? 'pb-2' : 'pb-4'}`}>
                {isActionable ? (
                    // 外側(relative, overflow可視)とカード本体(overflow-hidden)を分けているのは、
                    // 「UP!」ボーナスリボンをカードの角外側にはみ出させて表示するため
                    // (カード自身にoverflow-hiddenを付けているのはランダム演出・各種オーバーレイの
                    // 角丸クリップ用で、リボンをその内側に置くと角で切れてしまう)。
                    <div className="relative">
                    <div className={`relative rounded-2xl border p-3 overflow-hidden transition-all ${theme.spotlight}`}>
                        {/* ランダムクエストのキラキラ演出。#412(F-L9): 外部URL依存を廃止し
                            ネットワーク不要なCSSのみのドット柄(stardust風)にしてある。 */}
                        {isRandom && !isPending && (
                            <div
                                className="absolute inset-0 opacity-20 pointer-events-none"
                                style={{
                                    backgroundImage: 'radial-gradient(circle, rgba(255,255,255,0.9) 1px, transparent 1.5px)',
                                    backgroundSize: '14px 14px',
                                }}
                            ></div>
                        )}

                        {/* #391: 完了/取消APIの送信中オーバーレイ。 */}
                        {isProcessing && (
                            <div className="absolute inset-0 bg-black/40 z-20 flex items-center justify-center rounded-2xl cursor-wait" aria-busy="true">
                                <div className="bg-white/90 text-black px-3 py-1 rounded-full text-xs font-bold flex items-center gap-2 shadow-lg">
                                    <Loader2 size={16} className="animate-spin" />
                                    送信中...
                                </div>
                            </div>
                        )}

                        {/* クールダウン時のオーバーレイ: 残り時間を円形プログレスで可視化 */}
                        {isCooldown && (
                            <div className="absolute inset-0 bg-black/40 z-20 flex items-center justify-center rounded-2xl cursor-not-allowed">
                                <div className="bg-white/90 text-black px-3 py-1 rounded-full text-xs font-bold flex items-center gap-2 shadow-lg">
                                    <CooldownRing durationMs={COOLDOWN_MS} size={24} />
                                    Wait...
                                </div>
                            </div>
                        )}

                        {/* 長押し中のホールド進捗バー(取り消しジェスチャーのフィードバック) */}
                        {isPressing && (
                            <div className="absolute bottom-0 left-0 right-0 h-1 bg-red-950/60 z-30 rounded-b overflow-hidden">
                                <div
                                    className="h-full bg-red-400"
                                    style={{ width: `${pressProgress * 100}%`, transition: 'width 30ms linear' }}
                                />
                            </div>
                        )}

                        <div
                            onClick={canCancel ? undefined : handleTapComplete}
                            {...interactiveProps}
                            className={`relative z-10 ${!isEffectivelyLocked ? 'cursor-pointer active:scale-[0.98] select-none' : 'opacity-60 cursor-not-allowed'}`}
                        >
                            <div className="flex items-center gap-1.5 flex-wrap mb-1">
                                {visibleBadges.map(b => b.node)}
                                {hiddenBadgeCount > 0 && (
                                    <span className={`text-gray-400 ${badgeSizeClasses} px-1 font-bold`}>+{hiddenBadgeCount}</span>
                                )}
                            </div>
                            <div className="flex items-center gap-2 mb-1">
                                <div className={`font-black ${panelMode ? 'text-sm' : 'text-lg'} text-white min-w-0`}>
                                    {displayTitle}
                                </div>
                                {totalGold > 0 && (
                                    <span className={`font-mono text-xs font-bold whitespace-nowrap ml-auto ${hasBonus ? 'text-yellow-200 scale-110' : 'text-yellow-300'}`}>
                                        {totalGold} G
                                    </span>
                                )}
                            </div>
                            {/* 説明文: iconFirst(非識字年齢向け)では非表示にし、アイコンでの識別を優先する */}
                            {!iconFirst && quest.description && (
                                <div className={panelMode ? 'text-[10px] text-gray-400' : 'text-xs text-gray-400'}>
                                    {quest.description}
                                </div>
                            )}
                            {isPending && (
                                <div className="text-[10px] text-yellow-300 mt-1">確認待ち・長押しで取消</div>
                            )}
                        </div>
                    </div>

                    {/* ボーナス演出は「UP!」バッジのみに統一(カード全体の点滅アニメと二重に効いていたのを解消)。
                        カード本体の外側(overflow-hiddenでない側)に置き、角からはみ出させる。 */}
                    {hasBonus && !isPending && (
                        <div className="absolute -top-2 -right-2 bg-gradient-to-r from-red-600 to-orange-500 text-white text-[10px] font-bold px-2 py-0.5 rounded-full shadow-lg border border-white flex items-center gap-1 z-30 animate-bounce pointer-events-none">
                            <TrendingUp size={10} />
                            <span>UP!</span>
                        </div>
                    )}
                    </div>
                ) : (
                    // 完了済み・未開放は縮小した1行のみのミュート表示にする
                    // (RoutineStepRow の非カレント行と同じ扱い)。forceSlim(件数上限による
                    // 強制縮小)は isDone/isLocked のどちらでもなく、まだ実行可能なクエストが
                    // 縮小表示になっているだけなので、取り消し線を付けず(完了済みに見えて
                    // しまうため)、タップで完了確認を開けるようにする(カード表示側の
                    // handleTapComplete と同じ経路)。
                    <div
                        onClick={forceSlim ? handleTapComplete : undefined}
                        {...interactiveProps}
                        className={`flex flex-col gap-0.5 h-full ${(canCancel || forceSlim) ? 'cursor-pointer select-none' : ''}`}
                    >
                        <div className={`flex items-center gap-1.5 ${panelMode ? 'min-h-8 text-xs' : 'min-h-10 text-sm'} ${isLocked ? 'text-gray-500' : isDone ? 'text-gray-400 line-through decoration-2' : 'text-gray-300'}`}>
                            <span className="min-w-0 truncate">{displayTitle}</span>
                            {isLocked && <span className="text-[10px] text-gray-500 ml-1 flex-none">未開放</span>}
                            {isDone && (
                                isProcessing ? (
                                    <Loader2 size={12} className="animate-spin ml-auto flex-none text-gray-400" />
                                ) : (
                                    <span className="text-[10px] text-red-400 ml-auto flex-none flex items-center gap-1">
                                        <Undo2 size={11} />長押しで取消
                                    </span>
                                )
                            )}
                        </div>
                        {isPressing && (
                            <div className="h-0.5 bg-red-950/60 rounded-full overflow-hidden">
                                <div
                                    className="h-full bg-red-400"
                                    style={{ width: `${pressProgress * 100}%`, transition: 'width 30ms linear' }}
                                />
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
};

// 「もっと見る」用の折りたたみ状態(件数上限超過分のforceSlim対象・カットオフ位置)を、
// 必須クエスト・ボーナスクエストそれぞれの列に対して独立に算出する(要件確認済み、
// 2026-09-23: 常時表示は必須クエストのみにし、ボーナスクエストは折りたたみ表示にする)。
function computeOverflow(
    list: Quest[],
    currentUser: User,
    completedQuests: QuestHistory[],
    pendingQuests: QuestHistory[],
): { activeCount: number; forceSlimIds: Set<number>; overflowCount: number; cutoffQuestId?: number } {
    let activeCount = 0;
    let countedForLimit = 0;
    const forceSlim = new Set<number>();
    let cutoffId: number | undefined;
    for (const q of list) {
        const { isLocked, isDone, isPending } = getQuestLockState(q, currentUser, completedQuests, pendingQuests);
        if (isLocked || isDone) continue;
        activeCount++;
        if (isPending) continue; // 申請中は常にカード表示のままにする(件数上限の対象外)
        countedForLimit++;
        if (countedForLimit <= ACTIONABLE_CARD_LIMIT) {
            cutoffId = q.quest_id;
        } else if (q.quest_id !== undefined) {
            forceSlim.add(q.quest_id);
        }
    }
    return { activeCount, forceSlimIds: forceSlim, overflowCount: forceSlim.size, cutoffQuestId: cutoffId };
}

export default function QuestList({ quests, completedQuests, pendingQuests, currentUser, onQuestClick, completedSignal, processingQuestKeys, panelMode, iconFirst }: QuestListProps) {
    const sortedQuests = useMemo(() => {
        return quests.filter(q => {
            // ★変更: ターゲット判定 (role プレフィックスの対応)
            // #412(品質): 判定ロジックは lib/questTargeting.ts に集約(FamilyDashboard.tsx と共通)。
            if (!isQuestVisibleToUser(q, currentUser)) return false;

            // #412(F-L1): quest.days による曜日フィルタは削除した。サーバー側
            // (quest_service.py の filter_active_quests → _is_quest_currently_active)
            // が既にJST基準で day_of_week フィルタ済みの quests のみを返しているため、
            // ここで端末のローカルタイムゾーンを使って再フィルタすると、端末TZ≠JSTの
            // 時間帯(JSTの0〜9時に相当するUTC以西のTZ等)で当日のクエストが消えてしまう
            // (曜日境界の食い違い)。
            return true;
        }).sort((a, b) => {
            // ▼ ソート順: 進行中の期間限定 → 通常 → ロック中 → 承認待ち → 完了済み
            // （ロック/申請中/完了の判定は useQuestStatus と共通の getQuestLockState に集約。
            //  Hooksが使えないコンパレータからも直接呼べる）
            const getStatusScore = (quest: Quest) => {
                const { isLocked, isPending, isDone } =
                    getQuestLockState(quest, currentUser, completedQuests, pendingQuests);

                if (isDone) return 4;
                if (isPending) return 3;
                if (isLocked) return 2;
                if (quest.quest_type === 'limited') return 0; // 進行中の期間限定を最優先
                return 1; // 通常(無限・ランダム・特別バッジ付き含む)
            };

            const scoreA = getStatusScore(a);
            const scoreB = getStatusScore(b);

            if (scoreA !== scoreB) {
                return scoreA - scoreB;
            }

            // ... (ボーナス順などの既存ソート) ...
            const bonusA = (a.bonus_gold || 0) + (a.bonus_exp || 0);
            const bonusB = (b.bonus_gold || 0) + (b.bonus_exp || 0);
            if (bonusA !== bonusB) return bonusB - bonusA;
            // M-6-5バグ修正: 実カラムはquest_idであり、idは常にundefinedのため
            // (b.id as number) - (a.id as number) は常にNaNになり並び順が不定だった。
            // #291: idフィールド自体が幽霊フィールドとして型定義から削除されたため、
            // quest_idのみを参照する。
            const idA = Number(a.quest_id ?? 0);
            const idB = Number(b.quest_id ?? 0);
            return idB - idA;
        });
    }, [quests, currentUser, completedQuests, pendingQuests]);

    // 要件確認済み(2026-09-23): 常時表示は「毎日の必須クエスト」(required!==false)だけにし、
    // 「ボーナスクエスト」(required===false)はクエストタブ内の折りたたみセクションへ分ける。
    // required はサーバーが常に返すが、フォールバック用の疑似クエスト(masterData.js)等
    // 欠けている場合は必須側に倒す(undefinedを隠さないため)。
    const requiredQuests = useMemo(
        () => sortedQuests.filter(q => q.required !== false),
        [sortedQuests]
    );
    const bonusQuests = useMemo(
        () => sortedQuests.filter(q => q.required === false),
        [sortedQuests]
    );

    // 「今できること」が1件も無いかどうかだけを、案内メッセージ表示のために調べる
    // (角度①の名残)。完了済み・未開放クエスト自体は、すごろく風の1本のレールで
    // 常時インライン表示するため、以前のような表示/非表示のトグルはもう無い
    // (レール=路線図の接続線が、隠れたノードをまたぐのは不自然なため廃止した)。
    //
    // 角度②: 実行可能(申請中を除く)なクエストがACTIONABLE_CARD_LIMITを超える分は、
    // 「もっと見る」で展開するまでカード化しない(forceSlimIds)。cutoffQuestIdは
    // 「もっと見る」ボタンを差し込む位置(上限に達した直後のクエスト)を示す。
    // 必須クエスト・ボーナスクエストの列を分けたため、上限判定もそれぞれ独立に行う。
    const requiredOverflow = useMemo(
        () => computeOverflow(requiredQuests, currentUser, completedQuests, pendingQuests),
        [requiredQuests, currentUser, completedQuests, pendingQuests]
    );
    const bonusOverflow = useMemo(
        () => computeOverflow(bonusQuests, currentUser, completedQuests, pendingQuests),
        [bonusQuests, currentUser, completedQuests, pendingQuests]
    );
    const activeCount = requiredOverflow.activeCount + bonusOverflow.activeCount;

    // 一度「もっと見る」を開いたら、その画面を見ている間は展開したままにする
    // (折りたたみ直しのボタンは持たない。UI側の複雑さを避けるための単純化)。
    const [showAllRequired, setShowAllRequired] = useState(false);
    const [showAllBonus, setShowAllBonus] = useState(false);
    // ボーナスクエストのセクション自体の開閉(要件: 常時表示するのは必須クエストのみ)。
    // 既定は畳んだ状態にする。
    const [bonusExpanded, setBonusExpanded] = useState(false);

    const listContainerClass = panelMode
        ? 'flex flex-col animate-in fade-in duration-300'
        : 'flex flex-col animate-in fade-in slide-in-from-bottom-2 duration-300 pb-20';
    const headerClass = panelMode
        ? 'text-center border-b border-gray-600 pb-1 mb-2 text-yellow-300 text-xs font-bold'
        : 'text-center border-b border-gray-600 pb-1 mb-3 text-yellow-300 text-sm font-bold';

    const renderQuestCards = (
        list: Quest[],
        overflow: { forceSlimIds: Set<number>; overflowCount: number; cutoffQuestId?: number },
        showAll: boolean,
        onShowAll: () => void,
    ) => {
        const nodes: React.ReactNode[] = [];
        list.forEach((q, index) => {
            nodes.push(
                <motion.div
                    key={q.quest_id}
                    layout
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, x: -50, scale: 0.9, transition: { duration: 0.2 } }}
                    transition={{ type: "spring", stiffness: 300, damping: 25 }}
                >
                    <QuestItem
                        quest={q}
                        completedQuests={completedQuests}
                        pendingQuests={pendingQuests}
                        currentUser={currentUser}
                        onClick={onQuestClick}
                        completedSignal={completedSignal}
                        isProcessing={!!processingQuestKeys?.includes(getQuestProcessingKey(currentUser.user_id, q.quest_id))}
                        isLast={index === list.length - 1}
                        forceSlim={!showAll && overflow.forceSlimIds.has(q.quest_id ?? -1)}
                        panelMode={panelMode}
                        iconFirst={iconFirst}
                    />
                </motion.div>
            );

            // 上限に達した直後に「もっと見る」を差し込む。レールの接続線を途切れさせない
            // よう、ノード列を持つ疑似アイテムとして描画する(QuestItemのノード+線と同じ構造)。
            if (!showAll && overflow.overflowCount > 0 && q.quest_id === overflow.cutoffQuestId) {
                nodes.push(
                    <div key="more-toggle" className="flex gap-3">
                        <div className="flex flex-col items-center flex-none" style={{ width: panelMode ? 32 : 40 }}>
                            <div className={`rounded-full border-2 border-gray-600 bg-gray-800 text-gray-400 flex items-center justify-center flex-none ${panelMode ? 'w-8 h-8' : 'w-10 h-10'}`}>
                                <span className="text-lg leading-none">⋯</span>
                            </div>
                            <div className="w-[3px] flex-1 min-h-[12px] rounded-full bg-gray-700" />
                        </div>
                        <div className={`flex-1 min-w-0 flex items-center ${panelMode ? 'pb-2' : 'pb-4'}`}>
                            <button
                                type="button"
                                onClick={onShowAll}
                                className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-200"
                            >
                                <ChevronDown size={14} />
                                もっと見る ({overflow.overflowCount}件)
                            </button>
                        </div>
                    </div>
                );
            }
        });
        return <AnimatePresence mode="popLayout">{nodes}</AnimatePresence>;
    };

    return (
        <div className={listContainerClass}>
            {!panelMode && (
                <div className={headerClass}>
                    -- クエスト一覧 --
                </div>
            )}

            {sortedQuests.length === 0 && (
                <div className={panelMode ? 'text-center text-gray-400 py-6 text-xs' : 'text-center text-gray-400 py-10 text-sm'}>
                    現在挑戦できるクエストはありません
                </div>
            )}

            {sortedQuests.length > 0 && activeCount === 0 && (
                <div className={panelMode ? 'text-center text-gray-400 py-3 text-xs' : 'text-center text-gray-400 py-6 text-sm'}>
                    今できることはありません
                </div>
            )}

            {renderQuestCards(requiredQuests, requiredOverflow, showAllRequired, () => setShowAllRequired(true))}

            {bonusQuests.length > 0 && (
                <div className={panelMode ? 'mt-1' : 'mt-2'}>
                    <button
                        type="button"
                        onClick={() => setBonusExpanded(v => !v)}
                        className={`flex items-center gap-1.5 w-full text-left text-gray-400 hover:text-gray-200 border-t border-gray-700 pt-2 ${panelMode ? 'text-xs' : 'text-sm'}`}
                    >
                        <ChevronDown
                            size={14}
                            className={`transition-transform ${bonusExpanded ? 'rotate-180' : ''}`}
                        />
                        <span className="font-bold">ボーナスクエスト ({bonusQuests.length}件)</span>
                    </button>
                    {bonusExpanded && (
                        <div className="mt-2">
                            {renderQuestCards(bonusQuests, bonusOverflow, showAllBonus, () => setShowAllBonus(true))}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};
