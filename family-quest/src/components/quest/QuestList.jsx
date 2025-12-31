// family-quest/src/components/quest/QuestList.jsx
import React from 'react';
import { Undo2 } from 'lucide-react';

const QuestList = ({ quests, completedQuests, currentUser, onQuestClick }) => {
    const currentDay = new Date().getDay();

    // 1. フィルタリング
    const filteredQuests = quests.filter(q => {
        if (q.target !== 'all' && q.target !== currentUser?.user_id) return false;
        if (q.type === 'daily' && q.days) {
            if (!q.days || (Array.isArray(q.days) && q.days.length === 0)) return true;
            const dayList = Array.isArray(q.days) ? q.days : String(q.days).split(',').map(Number);
            return dayList.includes(currentDay);
        }
        return true;
    });

    // 2. ソート
    const sortedQuests = [...filteredQuests].sort((a, b) => {
        const aId = a.quest_id || a.id;
        const bId = b.quest_id || b.id;
        const aDone = completedQuests.some(cq => cq.user_id === currentUser?.user_id && cq.quest_id === aId);
        const bDone = completedQuests.some(cq => cq.user_id === currentUser?.user_id && cq.quest_id === bId);

        if (aDone !== bDone) return aDone ? 1 : -1;

        // 未完了同士なら時間限定を優先
        if (!aDone) {
            if (a.start_time && !b.start_time) return -1;
            if (!a.start_time && b.start_time) return 1;
        }
        return 0;
    });

    return (
        <div className="space-y-2 animate-in fade-in slide-in-from-bottom-2 duration-300">
            <div className="text-center border-b border-gray-600 pb-1 mb-2 text-yellow-300 text-sm font-bold">-- 本日の依頼 --</div>
            {sortedQuests.map(q => {
                const qId = q.quest_id || q.id;
                const isDone = completedQuests.some(cq =>
                    cq.user_id === currentUser?.user_id && cq.quest_id === qId
                );

                const isTimeLimited = !!q.start_time;
                const isRandom = q.type === 'random';
                const isLimited = q.type === 'limited';
                const isPersonal = q.target !== 'all';

                let containerClass = "border-white bg-blue-900/80 hover:bg-blue-800 hover:border-yellow-200";

                if (!isDone) {
                    if (isTimeLimited) {
                        containerClass = "border-orange-400 bg-gradient-to-r from-orange-900/90 to-red-900/90 hover:from-orange-800 hover:to-red-800 shadow-[0_0_10px_rgba(255,165,0,0.3)]";
                    } else if (isRandom) {
                        containerClass = "border-purple-400 bg-purple-950/90 hover:bg-purple-900";
                    } else if (isLimited) {
                        containerClass = "border-pink-400 bg-pink-950/90 hover:bg-pink-900";
                    }
                } else {
                    containerClass = "border-gray-600 bg-gray-900/50 grayscale";
                }

                return (
                    <div key={qId} onClick={() => onQuestClick(q)}
                        className={`border p-2 rounded flex justify-between items-center cursor-pointer select-none transition-all active:scale-[0.98] relative overflow-hidden ${containerClass}`}>

                        {isRandom && !isDone && <div className="absolute inset-0 bg-[url('https://www.transparenttextures.com/patterns/stardust.png')] opacity-20 pointer-events-none"></div>}

                        <div className="flex items-center gap-3 relative z-10">
                            <span className={`text-2xl ${isRandom && !isDone ? 'animate-bounce' : ''} ${isDone ? 'opacity-30' : ''}`}>{q.icon || q.icon_key}</span>
                            <div>
                                <div className="flex items-center gap-2 flex-wrap">
                                    {isTimeLimited && !isDone && (
                                        <span className="bg-yellow-500 text-black text-[10px] px-1.5 py-0.5 rounded font-bold animate-pulse flex items-center gap-1">
                                            ⏰ {q.start_time}~{q.end_time}
                                        </span>
                                    )}
                                    {isLimited && !isDone && <span className="bg-red-600 text-[10px] px-1 rounded font-bold">期間限定</span>}
                                    {isRandom && !isDone && <span className="bg-purple-600 text-[10px] px-1 rounded font-bold animate-pulse">レア出現!</span>}
                                    {isPersonal && !isDone && <span className="bg-blue-600 text-[10px] px-1 rounded">自分専用</span>}

                                    <div className={`font-bold ${isDone ? 'text-gray-500 line-through decoration-2' : 'text-white'}`}>{q.title}</div>
                                </div>
                                {!isDone && (
                                    <div className="flex gap-2 text-xs mt-0.5">
                                        <span className="text-orange-300 font-mono">EXP: {q.exp_gain || q.exp}</span>
                                        {(q.gold_gain || q.gold) > 0 && <span className="text-yellow-300 font-mono">{q.gold_gain || q.gold} G</span>}
                                    </div>
                                )}
                            </div>
                        </div>
                        {isDone && <span className="text-red-400 text-xs border border-red-500 px-1 py-0.5 rounded flex items-center gap-1"><Undo2 size={10} /> 戻す</span>}
                    </div>
                );
            })}
        </div>
    );
};

export default QuestList;