import React, { useMemo } from 'react';
import { Undo2, Clock, RotateCcw } from 'lucide-react';
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

    const {
        isDone, isPending, isInfinite, isRandom, isTimeLimited, isLimited,
        displayTitle, variant
    } = useQuestStatus({ quest, currentUser, completedQuests, pendingQuests });

    const handleClick = () => {
        // 音の再生ロジックはここで完結させる
        if (!isDone && !isPending) {
            // 完了または申請アクション
            if (quest.type === 'daily' || isInfinite) {
                play('clear'); // 完了音
            } else {
                play('submit'); // 申請音
            }
        }
        // 親から渡されたハンドラを実行
        onClick({ ...quest, _isInfinite: !!isInfinite });
    };

    return (
        <Card variant={variant} onClick={handleClick}>
            {/* ランダムクエストのキラキラ演出 */}
            {isRandom && !isDone && !isPending && (
                <div className="absolute inset-0 bg-[url('https://www.transparenttextures.com/patterns/stardust.png')] opacity-20 pointer-events-none"></div>
            )}

            <div className="flex items-center gap-3 relative z-10 w-full">
                {/* アイコン */}
                <span className={`text-2xl ${isInfinite ? 'text-cyan-200' : ''} ${isRandom && !isDone && !isPending ? 'animate-bounce' : ''} ${isDone ? 'opacity-30' : ''}`}>
                    {quest.icon || quest.icon_key}
                </span>

                {/* テキスト情報 */}
                <div className="flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                        {isInfinite && !isPending && (
                            <span className="bg-cyan-600 text-[10px] px-1 rounded font-bold flex items-center gap-0.5"><RotateCcw size={10} /> 無限</span>
                        )}

                        {isTimeLimited && !isDone && !isPending && (
                            <span className="bg-yellow-500 text-black text-[10px] px-1.5 py-0.5 rounded font-bold animate-pulse flex items-center gap-1">
                                ⏰ {quest.start_time}~{quest.end_time}
                            </span>
                        )}

                        {isLimited && !isDone && !isPending && (
                            <span className="bg-red-600 text-[10px] px-1 rounded font-bold">期間限定</span>
                        )}

                        {isPending && (
                            <span className="bg-yellow-500 text-black text-[10px] px-1 rounded font-bold animate-pulse flex items-center gap-1"><Clock size={10} /> 申請中</span>
                        )}

                        <div className={`font-bold ${isDone ? 'text-gray-500 line-through decoration-2' : 'text-white'}`}>
                            {displayTitle}
                        </div>
                    </div>
                    {(quest.desc || quest.description) && (
                        <div className="text-xs text-gray-400 mt-0.5 leading-tight">
                            {quest.desc || quest.description}
                        </div>
                    )}

                    {/* 報酬情報 (未完了時のみ) */}
                    {!isDone && !isPending && (
                        <div className="flex gap-2 text-xs mt-0.5">
                            <span className="text-orange-300 font-mono">EXP: {quest.exp_gain || quest.exp}</span>
                            {(quest.gold_gain || quest.gold || 0) > 0 && (
                                <span className="text-yellow-300 font-mono">{quest.gold_gain || quest.gold} G</span>
                            )}
                        </div>
                    )}
                </div>

                {/* 右側のステータス表示 */}
                {isDone && (
                    <span className="text-red-400 text-xs border border-red-500 px-1 py-0.5 rounded flex items-center gap-1 shrink-0">
                        <Undo2 size={10} /> 戻す
                    </span>
                )}
                {isPending && (
                    <span className="text-yellow-300 text-xs shrink-0">親の確認待ち...</span>
                )}
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
            return (b.id as number) - (a.id as number);
        });
    }, [quests, currentUser, currentDay]);

    // ★修正: ここにあった handleQuestClick ラッパーを削除しました。
    // 音の再生は QuestItem 側で行うため、ここでは onQuestClick をそのまま渡します。

    return (
        <div className="space-y-2 animate-in fade-in slide-in-from-bottom-2 duration-300 pb-20">
            <div className="text-center border-b border-gray-600 pb-1 mb-2 text-yellow-300 text-sm font-bold">
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
                    >
                        <QuestItem
                            quest={q}
                            completedQuests={completedQuests}
                            pendingQuests={pendingQuests}
                            currentUser={currentUser}
                            onClick={onQuestClick} // ★修正: 親のハンドラを直接渡す
                        />
                    </motion.div>
                ))}
            </AnimatePresence>

            {sortedQuests.length === 0 && (
                <div className="text-center text-gray-500 py-10 text-sm">
                    現在挑戦できるクエストはありません
                </div>
            )}
        </div>
    );
};