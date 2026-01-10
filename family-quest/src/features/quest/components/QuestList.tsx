import React, { useMemo } from 'react';
import { Undo2, Clock, RotateCcw } from 'lucide-react';
import { User, Quest, QuestHistory } from '@/types';
import { Card } from '@/components/ui/Card';
import { useQuestStatus } from '../hooks/useQuestStatus';

interface QuestListProps {
    quests: Quest[];
    completedQuests: QuestHistory[];
    pendingQuests: QuestHistory[];
    currentUser: User;
    onQuestClick: (quest: Quest) => void;
}

// 個別のクエストアイテムコンポーネント（リストの再描画を最適化するため分割）
const QuestItem: React.FC<{
    quest: Quest;
    completedQuests: QuestHistory[];
    pendingQuests: QuestHistory[];
    currentUser: User;
    onClick: (q: Quest) => void;
}> = ({ quest, completedQuests, pendingQuests, currentUser, onClick }) => {

    // ロジックはHookにお任せ
    const {
        isDone, isPending, isInfinite, isRandom, isTimeLimited, isLimited,
        displayTitle, variant
    } = useQuestStatus({ quest, currentUser, completedQuests, pendingQuests });

    // クリック時のハンドラ：無限フラグなどを付与して親に渡す
    const handleClick = () => {
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
    const currentDay = new Date().getDay();

    // フィルタリングとソート（表示順制御）
    const sortedQuests = useMemo(() => {
        return quests.filter(q => {
            // ターゲット限定 (user_id一致 or all)
            if (q.target && q.target !== 'all' && q.target !== currentUser?.user_id) return false;

            // 曜日限定
            if (q.type === 'daily' && q.days) {
                if (Array.isArray(q.days) && q.days.length === 0) return true;
                const dayList = Array.isArray(q.days) ? q.days : String(q.days).split(',').map(Number);
                if (!dayList.includes(currentDay)) return false;
            }
            return true;
        }).sort((a, b) => {
            // ※簡易ソート: 本来はHookのロジックを使って未完了を上に持ってくるべきですが、
            // ここでは一旦ID順やデータ順としています。
            // 必要であれば useQuestStatus を内部で使ってソートキーを作ることも可能です。
            return (b.id as number) - (a.id as number);
        });
    }, [quests, currentUser, currentDay]);

    return (
        <div className="space-y-2 animate-in fade-in slide-in-from-bottom-2 duration-300">
            <div className="text-center border-b border-gray-600 pb-1 mb-2 text-yellow-300 text-sm font-bold">
                -- 本日の依頼 --
            </div>

            {sortedQuests.map(q => (
                <QuestItem
                    key={q.id || q.quest_id}
                    quest={q}
                    completedQuests={completedQuests}
                    pendingQuests={pendingQuests}
                    currentUser={currentUser}
                    onClick={onQuestClick}
                />
            ))}
        </div>
    );
};