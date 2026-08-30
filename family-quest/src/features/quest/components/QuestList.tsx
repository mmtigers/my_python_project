import React, { useEffect, useMemo, useState } from 'react';
import { Undo2, Clock, TrendingUp, Lock, ChevronDown, ChevronUp } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { ID, User, Quest, QuestHistory } from '@/types';
import { Card } from '@/components/ui/Card';
import { CooldownRing } from '@/components/ui/CooldownRing';
import { useQuestStatus, getQuestLockState } from '../hooks/useQuestStatus';
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
    completedSignal: { id: ID; nonce: number } | null;
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

// 個別のクエストアイテムコンポーネント
const QuestItem: React.FC<{
    quest: Quest;
    completedQuests: QuestHistory[];
    pendingQuests: QuestHistory[];
    currentUser: User;
    onClick: (q: Quest) => void;
    completedSignal: { id: ID; nonce: number } | null;
    panelMode?: boolean;
    iconFirst?: boolean;
}> = ({ quest, completedQuests, pendingQuests, currentUser, onClick, completedSignal, panelMode, iconFirst }) => {

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
    const questId = quest.id ?? quest.quest_id;
    useEffect(() => {
        if (!isInfinite || !completedSignal || completedSignal.id !== questId) return;
        setIsCooldown(true);
        const timer = setTimeout(() => setIsCooldown(false), COOLDOWN_MS);
        return () => clearTimeout(timer);
    }, [completedSignal, isInfinite, questId]);

    // ボーナス計算
    const bonusGold = quest.bonus_gold || 0;
    const bonusExp = quest.bonus_exp || 0;
    const hasBonus = bonusGold > 0 || bonusExp > 0;

    // 合計報酬(ゴールドのみ画面表示する。EXPは表示不要のため計算しない)
    const baseGold = quest.gold_gain || quest.gold || 0;
    const totalGold = baseGold + bonusGold;

    const isSharedCompleted = !!quest.is_shared_completed_by && quest.is_shared_completed_by !== currentUser.user_id;
    const isSharedPending = !!quest.is_shared_pending_by && quest.is_shared_pending_by !== currentUser.user_id;
    const isSharedDoneByOther = isSharedCompleted || isSharedPending;
    const sharedName = quest.shared_completed_by_name || quest.shared_pending_by_name;
    const isEffectivelyLocked = isLocked || isSharedDoneByOther;

    // 完了済み/申請中の取り消しは「長押し」でのみ発火させ、うっかりタップでの
    // 誤取り消しを防ぐ。無限クエストは取り消し概念がないため対象外。
    const canCancel = !isInfinite && (isDone || isPending) && !isEffectivelyLocked;

    const runComplete = () => {
        // #102: 完了音・クールダウン開始はここでは行わない(上のuseEffect/App側を参照)。
        // ここではあくまで確認モーダルを開く(onClick)のみを行う。
        if (isCooldown || isEffectivelyLocked) return;
        onClick({ ...quest, _isInfinite: !!isInfinite });
    };

    const runCancel = () => {
        if (isEffectivelyLocked) return;
        play('cancel');
        onClick({ ...quest, _isInfinite: !!isInfinite });
    };

    const { isPressing, pressProgress, handlers: longPressHandlers } = useLongPress({
        onLongPress: runCancel,
        disabled: !canCancel,
        thresholdMs: 550,
    });

    // タップ即実行の対象は「まだ完了/申請していない」通常タップのみ。
    // 完了済み・申請中は canCancel 側(長押し)に処理を委ねる。
    const handleTapComplete = () => {
        if (canCancel || isCooldown) return; // 長押し対象/クールダウン中はタップでは何もしない
        runComplete();
    };

    // パネルモードでは viewport幅基準の md: 拡大/2カラム化には乗らず、
    // 常に「狭い列でも崩れず、かつタップしやすい(44px以上)」固定サイズを使う。
    // ★バグ修正: 1件あたりの表示が大きすぎた(アイコン・文字サイズ・カード高さ)ため、
    // 全体的にコンパクトにする。説明文は line-clamp を外し、見切れず全文表示する。
    // ★修正: アイコン周り(カード全体のp-2/md:p-6・列間のgap)の余白を半分程度に縮め、
    // 浮いた分をクエスト名(タイトル)・ゴールド表示エリアに回す。
    const cardSizeClasses = panelMode ? 'p-1 min-h-[56px]' : 'min-h-[56px] md:p-3 md:h-full';
    const layoutClasses = panelMode ? 'flex items-center gap-1' : 'flex md:grid md:grid-cols-[auto_1fr_auto] items-center gap-1.5 md:gap-3';
    const iconSizeClasses = panelMode ? (iconFirst ? 'text-4xl' : 'text-xl') : 'text-2xl md:text-5xl';
    const titleSizeClasses = panelMode ? (iconFirst ? 'text-xs' : 'text-sm') : 'text-sm md:text-xl';
    const descSizeClasses = panelMode ? 'text-[10px] text-gray-400 leading-tight' : 'text-xs md:text-sm text-gray-400 leading-tight md:leading-normal';
    const badgeSizeClasses = panelMode ? 'text-[10px]' : 'text-xs';
    const rewardSizeClasses = panelMode ? 'text-xs font-bold' : 'text-xs md:text-lg font-bold';
    const statusTextClasses = panelMode ? 'text-xs' : 'text-xs md:text-sm';

    // ▼ バッジ候補を優先度付きで作り、上位2件だけを表示する(角度①: バッジ過多の整理)
    const badgeCandidates: BadgeCandidate[] = [];
    if (isLocked && !isSharedDoneByOther) {
        badgeCandidates.push({
            key: 'locked', priority: 0, node: (
                <span key="locked" className={`bg-gray-500 text-white ${badgeSizeClasses} px-1.5 py-0.5 rounded font-bold flex items-center gap-0.5`}>
                    <Lock size={10} /> 未開放
                </span>
            )
        });
    }
    if (isSharedDoneByOther) {
        badgeCandidates.push({
            key: 'shared', priority: 1, node: (
                <span key="shared" className={`bg-gray-600 text-white ${badgeSizeClasses} px-1.5 py-0.5 rounded font-bold border border-gray-400`}>
                    {sharedName}が対応済み
                </span>
            )
        });
    }
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

    return (
        <div className="relative h-full group">
            <Card
                variant={variant}
                onClick={canCancel ? undefined : handleTapComplete}
                className={`${cardSizeClasses} transition-all duration-300 relative
                    ${!isEffectivelyLocked ? 'cursor-pointer active:scale-[0.98] select-none' : ''}
                    ${isEffectivelyLocked ? 'opacity-50 grayscale cursor-not-allowed bg-gray-200 border-gray-400' : ''}
                `}
                {...(canCancel ? longPressHandlers : {})}
            >
                {/* ランダムクエストのキラキラ演出 (Card内部でoverflow-hiddenされる) */}
                {isRandom && !isDone && !isPending && (
                    <div className="absolute inset-0 bg-[url('https://www.transparenttextures.com/patterns/stardust.png')] opacity-20 pointer-events-none"></div>
                )}

                {/* クールダウン時のオーバーレイ: 残り時間を円形プログレスで可視化 */}
                {isCooldown && (
                    <div className="absolute inset-0 bg-black/40 z-20 flex items-center justify-center rounded-lg cursor-not-allowed">
                        <div className="bg-white/90 text-black px-3 py-1 rounded-full text-xs font-bold flex items-center gap-2 shadow-lg">
                            <CooldownRing durationMs={COOLDOWN_MS} size={24} />
                            Wait...
                        </div>
                    </div>
                )}

                {/* 長押し中のホールド進捗バー(取り消しジェスチャーのフィードバック) */}
                {isPressing && (
                    <div className="absolute bottom-0 left-0 right-0 h-1.5 bg-red-950/60 z-30 rounded-b overflow-hidden">
                        <div
                            className="h-full bg-red-400"
                            style={{ width: `${pressProgress * 100}%`, transition: 'width 30ms linear' }}
                        />
                    </div>
                )}

                <div className={`${layoutClasses} relative z-10 w-full h-full`}>
                    {/* 1. アイコンエリア */}
                    <div className="flex items-center justify-center min-w-[1.5rem]">
                        {/* ▼ ロック時は鍵アイコンを表示 */}
                        {isLocked ? (
                            <span className={`${panelMode ? 'text-3xl' : 'text-2xl md:text-5xl'} text-gray-400`}>
                                <Lock size={panelMode ? 24 : 32} />
                            </span>
                        ) : (
                            <span className={`${iconSizeClasses} ${isInfinite ? 'text-cyan-200' : ''} ${isRandom && !isDone && !isPending ? 'animate-bounce' : ''} ${isDone ? 'opacity-30' : ''}`}>
                                {quest.icon || quest.icon_key}
                            </span>
                        )}
                    </div>

                    {/* 2. テキスト情報エリア */}
                    <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap mb-1">
                            {visibleBadges.map(b => b.node)}
                            {hiddenBadgeCount > 0 && (
                                <span className={`text-gray-400 ${badgeSizeClasses} px-1 font-bold`}>+{hiddenBadgeCount}</span>
                            )}
                        </div>

                        {/* タイトル */}
                        <div className={`font-bold ${titleSizeClasses} leading-snug mb-1 ${isDone ? 'text-gray-400 line-through decoration-2' : isLocked ? 'text-gray-400' : 'text-white'}`}>
                            {displayTitle}
                        </div>

                        {/* 説明文: iconFirst(非識字年齢向け)では非表示にし、アイコンでの識別を優先する */}
                        {!iconFirst && (quest.desc || quest.description) && (
                            <div className={descSizeClasses}>
                                {quest.desc || quest.description}
                            </div>
                        )}
                    </div>

                    {/* 3. 報酬・ステータスエリア */}
                    <div className="flex flex-col items-end justify-center gap-1 md:gap-2 min-w-[4rem]">
                        {/* ▼ ロック時の表示 */}
                        {isLocked ? (
                            <span className={`text-gray-400 ${statusTextClasses} whitespace-nowrap font-mono`}>
                                LOCKED
                            </span>
                        ) : isDone ? (
                            <div className="flex flex-col items-end gap-0.5">
                                <span className={`text-red-400 ${statusTextClasses} border border-red-500 px-2 py-1 rounded flex items-center gap-1 bg-red-950/30 whitespace-nowrap`}>
                                    <Undo2 size={panelMode ? 14 : 12} className="md:w-4 md:h-4" /> 長押しで取消
                                </span>
                            </div>
                        ) : isPending ? (
                            <div className="flex flex-col items-end gap-0.5">
                                <span className={`text-yellow-300 ${statusTextClasses} whitespace-nowrap`}>確認待ち</span>
                                <span className="text-[9px] text-gray-400 whitespace-nowrap">長押しで取消</span>
                            </div>
                        ) : (
                            totalGold > 0 && (
                                <div className="flex flex-col items-end">
                                    <span className={`font-mono ${rewardSizeClasses} whitespace-nowrap ${hasBonus ? 'text-yellow-200 scale-110' : 'text-yellow-300'}`}>
                                        {totalGold} G
                                    </span>
                                </div>
                            )
                        )}
                    </div>
                </div>
            </Card>

            {/* ボーナス演出は「UP!」バッジのみに統一(カード全体の点滅アニメと二重に効いていたのを解消) */}
            {hasBonus && !isDone && !isPending && (
                <div className="absolute -top-3 -right-2 bg-gradient-to-r from-red-600 to-orange-500 text-white text-xs font-bold px-2 py-1 rounded-full shadow-lg border border-white flex items-center gap-1 z-30 animate-bounce pointer-events-none">
                    <TrendingUp size={12} />
                    <span>UP!</span>
                </div>
            )}
        </div>
    );
};

export default function QuestList({ quests, completedQuests, pendingQuests, currentUser, onQuestClick, completedSignal, panelMode, iconFirst }: QuestListProps) {
    const jsDay = new Date().getDay();
    const currentDay = (jsDay + 6) % 7;
    const [showDoneAndLocked, setShowDoneAndLocked] = useState(false);

    const sortedQuests = useMemo(() => {
        return quests.filter(q => {
            // ★変更: ターゲット判定 (role プレフィックスの対応)
            if (q.target && q.target !== 'all') {
                if (q.target === 'siblings') {
                    // 兄妹連携クエスト: 対象は子ども(role_child)全員
                    if (currentUser.role !== 'role_child') return false;
                } else if (q.target.startsWith('role_')) {
                    if (currentUser.role !== q.target) return false;
                } else if (q.target !== currentUser?.user_id) {
                    return false;
                }
            }

            if (q.type === 'daily' && q.days) {
                if (Array.isArray(q.days) && q.days.length === 0) return true;
                const dayList = Array.isArray(q.days) ? q.days : String(q.days).split(',').map(Number);
                if (!dayList.includes(currentDay)) return false;
            }
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
                if (quest.type === 'limited') return 0; // 進行中の期間限定を最優先
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
            const idA = Number(a.quest_id ?? a.id ?? 0);
            const idB = Number(b.quest_id ?? b.id ?? 0);
            return idB - idA;
        });
    }, [quests, currentUser, currentDay, completedQuests, pendingQuests]);

    // ▼ 角度①: 「今できること」だけを最初に見せるため、完了済み/ロック中は折りたたむ。
    // 申請中(承認待ち)は本人がまだ気にする状態なので折りたたまず常時表示する。
    const { activeQuests, doneOrLockedQuests } = useMemo(() => {
        const active: Quest[] = [];
        const doneOrLocked: Quest[] = [];
        for (const q of sortedQuests) {
            const { isLocked, isDone } = getQuestLockState(q, currentUser, completedQuests, pendingQuests);
            if (isDone || isLocked) {
                doneOrLocked.push(q);
            } else {
                active.push(q);
            }
        }
        return { activeQuests: active, doneOrLockedQuests: doneOrLocked };
    }, [sortedQuests, currentUser, completedQuests, pendingQuests]);

    const listContainerClass = panelMode
        ? 'space-y-2 animate-in fade-in duration-300'
        : 'space-y-2 md:space-y-0 md:grid md:grid-cols-2 md:gap-6 animate-in fade-in slide-in-from-bottom-2 duration-300 pb-20';
    const headerClass = panelMode
        ? 'text-center border-b border-gray-600 pb-1 mb-2 text-yellow-300 text-xs font-bold'
        : 'md:col-span-2 text-center border-b border-gray-600 pb-1 mb-2 text-yellow-300 text-sm md:text-lg font-bold';

    const renderQuestCards = (list: Quest[]) => (
        <AnimatePresence mode="popLayout">
            {list.map(q => (
                <motion.div
                    key={q.id || q.quest_id}
                    layout
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, x: -50, scale: 0.9, transition: { duration: 0.2 } }}
                    transition={{ type: "spring", stiffness: 300, damping: 25 }}
                    className="h-full"
                >
                    <QuestItem
                        quest={q}
                        completedQuests={completedQuests}
                        pendingQuests={pendingQuests}
                        currentUser={currentUser}
                        onClick={onQuestClick}
                        completedSignal={completedSignal}
                        panelMode={panelMode}
                        iconFirst={iconFirst}
                    />
                </motion.div>
            ))}
        </AnimatePresence>
    );

    return (
        <div className={listContainerClass}>
            {!panelMode && (
                <div className={headerClass}>
                    -- クエスト一覧 --
                </div>
            )}

            {renderQuestCards(activeQuests)}

            {activeQuests.length === 0 && doneOrLockedQuests.length === 0 && (
                <div className={panelMode ? 'text-center text-gray-400 py-6 text-xs' : 'md:col-span-2 text-center text-gray-400 py-10 text-sm md:text-xl'}>
                    現在挑戦できるクエストはありません
                </div>
            )}

            {activeQuests.length === 0 && doneOrLockedQuests.length > 0 && (
                <div className={panelMode ? 'text-center text-gray-400 py-3 text-xs' : 'md:col-span-2 text-center text-gray-400 py-6 text-sm'}>
                    今できることはありません
                </div>
            )}

            {doneOrLockedQuests.length > 0 && (
                <div className={panelMode ? '' : 'md:col-span-2'}>
                    <button
                        onClick={() => setShowDoneAndLocked(v => !v)}
                        className="w-full min-h-[44px] flex items-center justify-center gap-1.5 text-xs text-gray-400 hover:text-gray-200 bg-black/20 hover:bg-black/30 rounded-lg py-2 transition-colors"
                    >
                        {showDoneAndLocked ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                        完了済み・未開放を{showDoneAndLocked ? '隠す' : '表示'} ({doneOrLockedQuests.length})
                    </button>

                    {showDoneAndLocked && (
                        <div className={panelMode ? 'space-y-2 mt-2' : 'space-y-2 md:space-y-0 md:grid md:grid-cols-2 md:gap-6 mt-2'}>
                            {renderQuestCards(doneOrLockedQuests)}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};
