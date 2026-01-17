import React, { useMemo, useState } from 'react';
import { Undo2, Clock, RotateCcw, Hourglass } from 'lucide-react';
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
        isDone, isPending, isInfinite, isRandom, isTimeLimited, isLimited,
        displayTitle, variant
    } = useQuestStatus({ quest, currentUser, completedQuests, pendingQuests });

    // ★追加: ボーナス計算
    const bonusGold = quest.bonus_gold || 0;
    const bonusExp = quest.bonus_exp || 0;
    const hasBonus = bonusGold > 0 || bonusExp > 0;

    // 合計報酬
    const baseGold = quest.gold_gain || quest.gold || 0;
    const baseExp = quest.exp_gain || quest.exp || 0;
    const totalGold = baseGold + bonusGold;
    const totalExp = baseExp + bonusExp;

    const handleClick = () => {
        if (isCooldown || isDone || isPending) return;

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
        onClick({ ...quest, _isInfinite: !!isInfinite });
    };

    return (
        <Card
            variant={variant}
            onClick={handleClick}
            // ★改良: タブレットでは高さ固定をやめ、paddingとGridでレイアウトを整える
            className="md:p-6 md:h-full"
        >
            {/* ランダムクエストのキラキラ演出 */}
            {isRandom && !isDone && !isPending && (
                <div className="absolute inset-0 bg-[url('https://www.transparenttextures.com/patterns/stardust.png')] opacity-20 pointer-events-none"></div>
            )}

            {/* ★追加: キャリーオーバーボーナスバッジ */}
            {hasBonus && !isDone && !isPending && (
                <div className="absolute -top-3 -right-2 bg-gradient-to-r from-red-600 to-orange-500 text-white text-xs font-bold px-2 py-1 rounded-full shadow-lg border border-white flex items-center gap-1 z-20 animate-bounce">
                    <TrendingUp size={12} />
                    <span>UP!</span>
                </div>
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

            {/* ★改良: レイアウト構造の変更 
                スマホ(初期値): flex (横並び)
                タブレット(md): grid (3列: アイコン / 内容 / 報酬・状態) 
            */}
            <div className="flex md:grid md:grid-cols-[auto_1fr_auto] items-center gap-3 md:gap-6 relative z-10 w-full h-full">

                {/* 1. アイコンエリア */}
                <div className="flex items-center justify-center min-w-[3rem]">
                    <span className={`text-2xl md:text-5xl ${isInfinite ? 'text-cyan-200' : ''} ${isRandom && !isDone && !isPending ? 'animate-bounce' : ''} ${isDone ? 'opacity-30' : ''}`}>
                        {quest.icon || quest.icon_key}
                    </span>
                </div>

                {/* 2. テキスト情報エリア */}
                <div className="flex-1 min-w-0"> {/* min-w-0はテキストの折り返しに必要 */}
                    <div className="flex items-center gap-2 flex-wrap mb-1">
                        {/* バッジ類 */}
                        {isInfinite && !isPending && (
                            <span className="bg-cyan-600 text-[10px] md:text-xs px-1.5 py-0.5 rounded font-bold flex items-center gap-0.5"><RotateCcw size={10} /> 無限</span>
                        )}
                        {isTimeLimited && !isDone && !isPending && (
                            <span className="bg-yellow-500 text-black text-[10px] md:text-xs px-1.5 py-0.5 rounded font-bold animate-pulse flex items-center gap-1">
                                ⏰ {quest.start_time}~{quest.end_time}
                            </span>
                        )}
                        {isLimited && !isDone && !isPending && (
                            <span className="bg-red-600 text-[10px] md:text-xs px-1.5 py-0.5 rounded font-bold">期間限定</span>
                        )}
                        {isPending && (
                            <span className="bg-yellow-500 text-black text-[10px] md:text-xs px-1.5 py-0.5 rounded font-bold animate-pulse flex items-center gap-1"><Clock size={10} /> 申請中</span>
                        )}
                    </div>

                    {/* タイトル */}
                    <div className={`font-bold text-sm md:text-xl leading-snug mb-1 ${isDone ? 'text-gray-500 line-through decoration-2' : 'text-white'}`}>
                        {displayTitle}
                    </div>

                    {/* 説明文 */}
                    {(quest.desc || quest.description) && (
                        <div className="text-xs md:text-sm text-gray-400 leading-tight md:leading-normal">
                            {quest.desc || quest.description}
                        </div>
                    )}
                </div>

                {/* 3. 報酬・ステータスエリア */}
                <div className="flex flex-col items-end justify-center gap-1 md:gap-2 min-w-[4rem]">
                    {isDone ? (
                        <span className="text-red-400 text-xs md:text-base border border-red-500 px-2 py-1 rounded flex items-center gap-1 bg-red-950/30 whitespace-nowrap">
                            <Undo2 size={12} className="md:w-4 md:h-4" /> 戻す
                        </span>
                    ) : isPending ? (
                        <span className="text-yellow-300 text-xs md:text-sm whitespace-nowrap">確認待ち</span>
                    ) : (
                        // 未完了時の報酬表示（ボーナス時は赤字で強調）
                        <div className="flex flex-col items-end">
                            <span className={`font-mono text-xs md:text-lg font-bold whitespace-nowrap ${hasBonus ? 'text-orange-400 scale-110' : 'text-orange-300'}`}>
                                EXP +{totalExp}
                            </span>
                            {totalGold > 0 && (
                                <span className={`font-mono text-xs md:text-lg font-bold whitespace-nowrap ${hasBonus ? 'text-yellow-200 scale-110' : 'text-yellow-300'}`}>
                                    {totalGold} G
                                </span>
                            )}
                            {/* ボーナス内訳の小文字表示 */}
                            {hasBonus && (
                                <span className="text-[10px] md:text-xs text-red-300 font-bold animate-pulse">
                                    (Bonus +{bonusGold}G)
                                </span>
                            )}
                        </div>
                    )}
                </div>
            </div>
        </Card>
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
            // ボーナスがあるものを優先的に上に表示する
            const bonusA = (a.bonus_gold || 0) + (a.bonus_exp || 0);
            const bonusB = (b.bonus_gold || 0) + (b.bonus_exp || 0);
            if (bonusA !== bonusB) return bonusB - bonusA;

            return (b.id as number) - (a.id as number);
        });
    }, [quests, currentUser, currentDay]);

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