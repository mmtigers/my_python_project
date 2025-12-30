import React from 'react';
import { Trophy, Coins, Star, History } from 'lucide-react';

/**
 * 家族全体の記録（統計とタイムライン）を表示するコンポーネント
 */
const FamilyLog = ({ stats, chronicle }) => {
    if (!stats || !chronicle) return <div className="text-center py-10">冒険の記録を読み込んでいます...</div>;

    // 日付ごとにログをグループ化
    const groupedChronicle = chronicle.reduce((groups, item) => {
        const date = item.dateStr;
        if (!groups[date]) groups[date] = [];
        groups[date].push(item);
        return groups;
    }, {});

    return (
        <div className="space-y-4 animate-in fade-in duration-500 pb-6">
            {/* 1. 家族の総力（統計エリア） */}
            <div className="grid grid-cols-2 gap-2">
                <div className="bg-gradient-to-br from-indigo-900 to-blue-900 border-2 border-yellow-500 rounded-lg p-3 shadow-lg flex flex-col items-center">
                    <Trophy className="text-yellow-400 mb-1" size={24} />
                    <div className="text-[10px] text-blue-200 uppercase font-bold">Party Rank</div>
                    <div className="text-sm font-bold text-white text-center">{stats.partyRank}</div>
                </div>
                <div className="bg-gradient-to-br from-gray-900 to-blue-900 border-2 border-white rounded-lg p-3 shadow-lg flex flex-col items-center">
                    <div className="flex gap-2 mb-1">
                        <div className="flex flex-col items-center">
                            <Star className="text-cyan-400" size={16} />
                            <span className="text-[10px] text-white">Lv.{stats.totalLevel}</span>
                        </div>
                        <div className="flex flex-col items-center">
                            <Coins className="text-yellow-400" size={16} />
                            <span className="text-[10px] text-white">{stats.totalGold} G</span>
                        </div>
                    </div>
                    <div className="text-[10px] text-blue-200 uppercase font-bold">Total Power</div>
                    <div className="text-xs text-white">達成数: {stats.totalQuests}</div>
                </div>
            </div>

            {/* 2. タイムライン */}
            <div className="space-y-4">
                <div className="flex items-center gap-2 text-yellow-300 font-bold text-sm border-b border-gray-700 pb-1">
                    <History size={16} />
                    <span>冒険のあしあと</span>
                </div>

                {Object.entries(groupedChronicle).map(([date, items]) => (
                    <div key={date} className="space-y-2">
                        <div className="text-[10px] bg-gray-800 text-gray-400 w-fit px-2 py-0.5 rounded-full border border-gray-700">
                            {date}
                        </div>
                        <div className="space-y-2 ml-2 border-l-2 border-gray-800 pl-4">
                            {items.map((log, idx) => (
                                <div key={idx} className="relative flex items-start gap-3 bg-blue-950/30 p-2 rounded border border-blue-900/50">
                                    <span className="text-xl">{log.userAvatar}</span>
                                    <div className="flex-1">
                                        <div className="text-xs text-white leading-relaxed">
                                            {log.text}
                                        </div>
                                        <div className="flex gap-2 mt-1">
                                            {log.gold > 0 && (
                                                <span className="text-[9px] text-yellow-400 font-bold bg-yellow-900/30 px-1 rounded">
                                                    +{log.gold} G
                                                </span>
                                            )}
                                            {log.exp > 0 && (
                                                <span className="text-[9px] text-cyan-400 font-bold bg-cyan-900/30 px-1 rounded">
                                                    +{log.exp} Exp
                                                </span>
                                            )}
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
};

export default FamilyLog;