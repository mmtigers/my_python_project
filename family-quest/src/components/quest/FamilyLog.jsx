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
                <div className="bg-gradient-to-br from-gray-900 to-blue-900 border border-gray-600 rounded-lg p-3 shadow flex flex-col justify-center gap-1">
                    <div className="flex justify-between text-xs">
                        <span className="text-gray-400">Total Levels</span>
                        <span className="text-white font-bold">{stats.totalLevel}</span>
                    </div>
                    <div className="flex justify-between text-xs">
                        <span className="text-gray-400">Total Quests</span>
                        <span className="text-white font-bold">{stats.totalQuests}</span>
                    </div>
                    <div className="flex justify-between text-xs items-center">
                        <span className="text-gray-400 flex items-center gap-1"><Coins size={10} /> Gold</span>
                        <span className="text-yellow-400 font-bold">{stats.totalGold.toLocaleString()}</span>
                    </div>
                </div>
            </div>

            {/* 2. 冒険の記録（タイムライン） */}
            <div className="space-y-4">
                <div className="flex items-center gap-2 text-white border-b border-gray-700 pb-2">
                    <History className="text-blue-400" />
                    <h3 className="font-bold text-lg">冒険の記録</h3>
                </div>

                {Object.entries(groupedChronicle).map(([date, logs]) => (
                    <div key={date} className="relative pl-4 border-l-2 border-gray-700">
                        <div className="absolute -left-[9px] top-0 w-4 h-4 rounded-full bg-blue-500 border-4 border-black"></div>
                        <div className="text-xs text-gray-400 mb-2 font-bold">{date}</div>

                        <div className="space-y-2">
                            {logs.map((log) => {
                                // ★追加: 画像アバター判定
                                const isImage = log.userAvatar && (log.userAvatar.startsWith('/uploads') || log.userAvatar.startsWith('http'));

                                return (
                                    <div key={log.timestamp} className="flex items-start gap-3 bg-blue-950/30 p-2 rounded border border-blue-900/50">
                                        {/* ★変更: アバター表示エリア */}
                                        <div className="flex-shrink-0 w-8 h-8 bg-gray-900 rounded-full border border-gray-600 flex items-center justify-center overflow-hidden">
                                            {isImage ? (
                                                <img src={log.userAvatar} alt="avatar" className="w-full h-full object-cover" />
                                            ) : (
                                                <span className="text-xl">{log.userAvatar}</span>
                                            )}
                                        </div>

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
                                );
                            })}
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
};

export default FamilyLog;