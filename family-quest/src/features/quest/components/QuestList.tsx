import React, { useMemo, useState } from 'react';
import { Undo2, Clock, RotateCcw, Hourglass, TrendingUp, Lock } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { User, Quest, QuestHistory } from '@/types';
import { Card } from '@/components/ui/Card';
import { useQuestStatus } from '../hooks/useQuestStatus';
import { useSound } from '@/hooks/useSound';

interface QuestListProps {
    quests: Quest[];
    completedQuests: QuestHistory[];
    pendingQuests: QuestHistory[];
    currentUser: User;
    onQuestClick: (quest: Quest) => void;
}

// 個別のクエストアイテムコンポーネント
const QuestItem: React.FC<{
    quest: Quest;
    completedQuests: QuestHistory[];
    pendingQuests: QuestHistory[];
    currentUser: User;
    onClick: (q: Quest) => void;
}> = ({ quest, completedQuests, pendingQuests, currentUser, onClick }) => {

    const { play } = useSound();
    const [isCooldown, setIsCooldown] = useState(false);

    const {
        isDone, isPending, isInfinite, isRandom, isTimeLimited, isLimited, isLocked,
        displayTitle, variant
    } = useQuestStatus({ quest, currentUser, completedQuests, pendingQuests });

    // ボーナス計算
    const bonusGold = quest.bonus_gold || 0;
    const bonusExp = quest.bonus_exp || 0;
    const hasBonus = bonusGold > 0 || bonusExp > 0;

    // 合計報酬
    const baseGold = quest.gold_gain || quest.gold || 0;
    const baseExp = quest.exp_gain || quest.exp || 0;
    const totalGold = baseGold + bonusGold;
    const totalExp = baseExp + bonusExp;

    const handleClick = () => {
        if (isCooldown) return;
        if (isLocked) return;

        if (!isDone && !isPending) {
            if (quest.type === 'daily' || isInfinite) {
                play('clear');
            } else {
                play('submit');
            }

            if (isInfinite) {
                setIsCooldown(true);
                setTimeout(() => {
                    setIsCooldown(false);
                }, 60000);
            }
        }

        // 【重要】 完了済みであっても onClick を発火させ、親側でキャンセル処理を行えるようにする
        onClick({ ...quest, _isInfinite: !!isInfinite });
    };

    // ★ここで handleClick を終了し、コンポーネントの描画結果を return します
    return (
        <div className="relative h-full group">
            <Card
                variant={variant}
                onClick={handleClick}
                // ボーナス時の赤枠アニメーションはCard自体に適用
                className={`md:p-6 md:h-full 
                    ${hasBonus && !isDone && !isPending && !isLocked ? 'border-2 border-red-400 animate-pulse-slow' : ''}
                    ${isLocked ? 'opacity-60 grayscale cursor-not-allowed bg-gray-100 border-gray-300' : ''}
                `}
            >
                {/* ランダムクエストのキラキラ演出 (Card内部でoverflow-hiddenされる) */}
                {isRandom && !isDone && !isPending && (
                    <div className="absolute inset-0 bg-[url('https://www.transparenttextures.com/patterns/stardust.png')] opacity-20 pointer-events-none"></div>
                )}

                {/* クールダウン時のオーバーレイ */}
                {isCooldown && (
                    <div className="absolute inset-0 bg-black/40 z-20 flex items-center justify-center rounded-lg animate-pulse cursor-not-allowed">
                        <div className="bg-white/90 text-black px-3 py-1 rounded-full text-xs font-bold flex items-center gap-2 shadow-lg">
                            <Hourglass size={12} className="animate-spin" />
                            Wait...
                        </div>
                    </div>
                )}

                <div className="flex md:grid md:grid-cols-[auto_1fr_auto] items-center gap-3 md:gap-6 relative z-10 w-full h-full">
                    {/* 1. アイコンエリア */}
                    <div className="flex items-center justify-center min-w-[3rem]">
                        {/* ▼ ロック時は鍵アイコンを表示 */}
                        {isLocked ? (
                            <span className="text-2xl md:text-5xl text-gray-400">
                                <Lock size={32} />
                            </span>
                        ) : (
                            <span className={`text-2xl md:text-5xl ${isInfinite ? 'text-cyan-200' : ''} ${isRandom && !isDone && !isPending ? 'animate-bounce' : ''} ${isDone ? 'opacity-30' : ''}`}>
                                {quest.icon || quest.icon_key}
                            </span>
                        )}
                    </div>

                    {/* 2. テキスト情報エリア */}
                    <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap mb-1">
                            {/* ▼ ロックバッジ */}
                            {isLocked && (
                                <span className="bg-gray-500 text-white text-[10px] md:text-xs px-1.5 py-0.5 rounded font-bold flex items-center gap-0.5">
                                    <Lock size={10} /> 未開放
                                </span>
                            )}

                            {/* ... (他のバッジ: 無限、時間制限、申請中) ... */}
                            {isInfinite && !isPending && !isLocked && (
                                <span className="bg-cyan-600 text-[10px] md:text-xs px-1.5 py-0.5 rounded font-bold flex items-center gap-0.5"><RotateCcw size={10} /> 無限</span>
                            )}
                            {isTimeLimited && !isDone && !isPending && !isLocked && (
                                <span className="bg-yellow-500 text-black text-[10px] md:text-xs px-1.5 py-0.5 rounded font-bold animate-pulse flex items-center gap-1">
                                    ⏰ {quest.start_time}~{quest.end_time}
                                </span>
                            )}
                            {isLimited && !isDone && !isPending && !isLocked && (
                                <span className="bg-red-600 text-[10px] md:text-xs px-1.5 py-0.5 rounded font-bold">期間限定</span>
                            )}
                            {isPending && (
                                <span className="bg-yellow-500 text-black text-[10px] md:text-xs px-1.5 py-0.5 rounded font-bold animate-pulse flex items-center gap-1"><Clock size={10} /> 申請中</span>
                            )}
                        </div>

                        {/* タイトル */}
                        <div className={`font-bold text-sm md:text-xl leading-snug mb-1 ${isDone ? 'text-gray-500 line-through decoration-2' : isLocked ? 'text-gray-500' : 'text-white'}`}>
                            {displayTitle}
                        </div>

                        {/* 説明文: ロック時は「条件: 〇〇」と出しても親切だが、一旦そのまま表示 */}
                        {(quest.desc || quest.description) && (
                            <div className="text-xs md:text-sm text-gray-400 leading-tight md:leading-normal">
                                {quest.desc || quest.description}
                            </div>
                        )}
                    </div>

                    {/* 3. 報酬・ステータスエリア */}
                    <div className="flex flex-col items-end justify-center gap-1 md:gap-2 min-w-[4rem]">
                        {/* ▼ ロック時の表示 */}
                        {isLocked ? (
                            <span className="text-gray-400 text-xs md:text-sm whitespace-nowrap font-mono">
                                LOCKED
                            </span>
                        ) : isDone ? (
                            <span className="text-red-400 text-xs md:text-base border border-red-500 px-2 py-1 rounded flex items-center gap-1 bg-red-950/30 whitespace-nowrap">
                                <Undo2 size={12} className="md:w-4 md:h-4" /> 戻す
                            </span>
                        ) : isPending ? (
                            <span className="text-yellow-300 text-xs md:text-sm whitespace-nowrap">確認待ち</span>
                        ) : (
                            <div className="flex flex-col items-end">
                                <span className={`font-mono text-xs md:text-lg font-bold whitespace-nowrap ${hasBonus ? 'text-orange-400 scale-110' : 'text-orange-300'}`}>
                                    EXP +{totalExp}
                                </span>
                                {totalGold > 0 && (
                                    <span className={`font-mono text-xs md:text-lg font-bold whitespace-nowrap ${hasBonus ? 'text-yellow-200 scale-110' : 'text-yellow-300'}`}>
                                        {totalGold} G
                                    </span>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            </Card>

            {/* バッジ */}
            {hasBonus && !isDone && !isPending && (
                <div className="absolute -top-3 -right-2 bg-gradient-to-r from-red-600 to-orange-500 text-white text-xs font-bold px-2 py-1 rounded-full shadow-lg border border-white flex items-center gap-1 z-30 animate-bounce pointer-events-none">
                    <TrendingUp size={12} />
                    <span>UP!</span>
                </div>
            )}
        </div>
    );
};

export default function QuestList({ quests, completedQuests, pendingQuests, currentUser, onQuestClick }: QuestListProps) {
    const jsDay = new Date().getDay();
    const currentDay = (jsDay + 6) % 7;

    const sortedQuests = useMemo(() => {
        return quests.filter(q => {
            if (q.target && q.target !== 'all' && q.target !== currentUser?.user_id) return false;
            if (q.type === 'daily' && q.days) {
                if (Array.isArray(q.days) && q.days.length === 0) return true;
                const dayList = Array.isArray(q.days) ? q.days : String(q.days).split(',').map(Number);
                if (!dayList.includes(currentDay)) return false;
            }
            return true;
        }).sort((a, b) => {
            // ▼ ソート順のロジック更新
            const getStatusScore = (quest: Quest) => {
                const qId = quest.id || quest.quest_id;

                // 1. ロック判定 (Hooksが使えないのでここで簡易判定)
                const preReqId = quest.pre_requisite_quest_id;
                const isPreReqCleared = !preReqId || completedQuests.some(cq =>
                    cq.user_id === currentUser.user_id &&
                    cq.quest_id === preReqId &&
                    cq.status === 'approved'
                );
                const isLocked = !isPreReqCleared;

                const isInfinite = quest.type === 'infinite' || quest.quest_type === 'infinite' || (quest as any)._isInfinite;
                if (isInfinite) return 0; // 無限は最優先(挑戦可能)

                const isPending = pendingQuests.some(pq => pq.user_id === currentUser.user_id && pq.quest_id === qId);
                const isDone = completedQuests.some(cq => cq.user_id === currentUser.user_id && cq.quest_id === qId && cq.status === 'approved');

                // 優先順位: 
                // 0: 未完了(挑戦可能)
                // 1: 申請中 (目立つように上の方へ、または完了の前へ)
                // 2: ロック済み (これからやるものだが今はできない -> 下へ)
                // 3: 完了済み (一番下)

                if (isPending) return 1;
                if (isLocked) return 2;  // ▼ ロックは未完了の後、完了の前
                if (isDone) return 3;

                return 0; // 未完了・挑戦可能
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
            return (b.id as number) - (a.id as number);
        });
    }, [quests, currentUser, currentDay, completedQuests, pendingQuests]);

    return (
        <div className="space-y-2 md:space-y-0 md:grid md:grid-cols-2 md:gap-6 animate-in fade-in slide-in-from-bottom-2 duration-300 pb-20">
            <div className="md:col-span-2 text-center border-b border-gray-600 pb-1 mb-2 text-yellow-300 text-sm md:text-lg font-bold">
                -- 本日の依頼 --
            </div>

            <AnimatePresence mode="popLayout">
                {sortedQuests.map(q => (
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
                        />
                    </motion.div>
                ))}
            </AnimatePresence>

            {sortedQuests.length === 0 && (
                <div className="md:col-span-2 text-center text-gray-500 py-10 text-sm md:text-xl">
                    現在挑戦できるクエストはありません
                </div>
            )}
        </div>
    );
};