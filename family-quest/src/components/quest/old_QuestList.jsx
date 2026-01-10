import React from 'react';
import { Undo2, Clock, CheckCircle2, RotateCcw } from 'lucide-react';

const QuestList = ({ quests, completedQuests, pendingQuests = [], currentUser, onQuestClick }) => {
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

    // ヘルパー関数: 状態判定と「自分だけの回数」計算
    const checkStatus = (quest) => {
        const qId = quest.quest_id || quest.id;
        // マスタデータ上の type を確認
        const isInfinite = quest.type === 'infinite' || quest.quest_type === 'infinite';

        // 自分の完了履歴を検索
        const myCompletions = completedQuests.filter(cq =>
            cq.user_id === currentUser?.user_id &&
            cq.quest_id === qId &&
            cq.status === 'approved' // 承認済みのみカウント
        );

        let isDone = myCompletions.length > 0;
        if (isInfinite) isDone = false; // 無限クエストは常に未完了扱い

        const isPending = pendingQuests.some(pq => pq.user_id === currentUser?.user_id && pq.quest_id === qId);

        // 無限クエストの場合の表示用タイトル
        let displayTitle = quest.title;
        if (isInfinite) {
            const count = myCompletions.length + 1; // 次の回数
            displayTitle = `${quest.title} (${count}回目)`;
        }

        return { isDone, isPending, isInfinite, displayTitle };
    };

    // 2. ソート
    const sortedQuests = [...filteredQuests].sort((a, b) => {
        const statusA = checkStatus(a);
        const statusB = checkStatus(b);

        const aFinished = statusA.isDone || statusA.isPending;
        const bFinished = statusB.isDone || statusB.isPending;

        if (aFinished !== bFinished) return aFinished ? 1 : -1;
        if (!aFinished) {
            if (a.start_time && !b.start_time) return -1;
            if (!a.start_time && b.start_time) return 1;
        }
        return 0;
    });

    return (
        <div className="space-y-2 animate-in fade-in slide-in-from-bottom-2 duration-300">
            <div className="text-center border-b border-gray-600 pb-1 mb-2 text-yellow-300 text-sm font-bold">-- 本日の依頼 --</div>
            {sortedQuests.map(q => {
                const { isDone, isPending, isInfinite, displayTitle } = checkStatus(q);

                const isTimeLimited = !!q.start_time;
                const isRandom = q.type === 'random';
                const isLimited = q.type === 'limited';

                let containerClass = "border-white bg-blue-900/80 hover:bg-blue-800 hover:border-yellow-200";

                if (isDone) {
                    containerClass = "border-gray-600 bg-gray-900/50 grayscale";
                } else if (isPending) {
                    containerClass = "border-yellow-500 bg-yellow-900/40";
                } else {
                    if (isInfinite) {
                        containerClass = "border-cyan-400 bg-cyan-950/90 hover:bg-cyan-900 shadow-[0_0_8px_rgba(0,255,255,0.2)]";
                    } else if (isTimeLimited) {
                        containerClass = "border-orange-400 bg-gradient-to-r from-orange-900/90 to-red-900/90 hover:from-orange-800 hover:to-red-800 shadow-[0_0_10px_rgba(255,165,0,0.3)]";
                    } else if (isRandom) {
                        containerClass = "border-purple-400 bg-purple-950/90 hover:bg-purple-900";
                    } else if (isLimited) {
                        containerClass = "border-pink-400 bg-pink-950/90 hover:bg-pink-900";
                    }
                }

                return (
                    <div
                        key={q.quest_id || q.id}
                        // ★重要修正: ここで _isInfinite フラグを確実に埋め込んで渡す
                        onClick={() => onQuestClick({ ...q, _isInfinite: isInfinite })}
                        className={`border p-2 rounded flex justify-between items-center cursor-pointer select-none transition-all active:scale-[0.98] relative overflow-hidden ${containerClass}`}
                    >
                        {isRandom && !isDone && !isPending && <div className="absolute inset-0 bg-[url('https://www.transparenttextures.com/patterns/stardust.png')] opacity-20 pointer-events-none"></div>}

                        <div className="flex items-center gap-3 relative z-10">
                            <span className={`text-2xl ${isInfinite ? 'text-cyan-200' : ''} ${isRandom && !isDone && !isPending ? 'animate-bounce' : ''} ${isDone ? 'opacity-30' : ''}`}>
                                {q.icon || q.icon_key}
                            </span>
                            <div>
                                <div className="flex items-center gap-2 flex-wrap">
                                    {isInfinite && !isPending && <span className="bg-cyan-600 text-[10px] px-1 rounded font-bold flex items-center gap-0.5"><RotateCcw size={10} /> 無限</span>}

                                    {isTimeLimited && !isDone && !isPending && (
                                        <span className="bg-yellow-500 text-black text-[10px] px-1.5 py-0.5 rounded font-bold animate-pulse flex items-center gap-1">
                                            ⏰ {q.start_time}~{q.end_time}
                                        </span>
                                    )}
                                    {isLimited && !isDone && !isPending && <span className="bg-red-600 text-[10px] px-1 rounded font-bold">期間限定</span>}

                                    {isPending && <span className="bg-yellow-500 text-black text-[10px] px-1 rounded font-bold animate-pulse flex items-center gap-1"><Clock size={10} /> 申請中</span>}

                                    <div className={`font-bold ${isDone ? 'text-gray-500 line-through decoration-2' : 'text-white'}`}>{displayTitle}</div>
                                </div>
                                {!isDone && !isPending && (
                                    <div className="flex gap-2 text-xs mt-0.5">
                                        <span className="text-orange-300 font-mono">EXP: {q.exp_gain || q.exp}</span>
                                        {(q.gold_gain || q.gold) > 0 && <span className="text-yellow-300 font-mono">{q.gold_gain || q.gold} G</span>}
                                    </div>
                                )}
                            </div>
                        </div>
                        {isDone && <span className="text-red-400 text-xs border border-red-500 px-1 py-0.5 rounded flex items-center gap-1"><Undo2 size={10} /> 戻す</span>}
                        {isPending && <span className="text-yellow-300 text-xs">親の確認待ち...</span>}
                    </div>
                );
            })}
        </div>
    );
};

export default QuestList;