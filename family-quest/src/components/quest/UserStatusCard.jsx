// family-quest/src/components/quest/UserStatusCard.jsx
import React from 'react';
import { Crown } from 'lucide-react';

const UserStatusCard = ({ user }) => {
    if (!user) return null;

    const expPercentage = ((user.exp || 0) / (user.nextLevelExp || 100)) * 100;
    const expRemaining = (user.nextLevelExp || 100) - (user.exp || 0);

    return (
        <div className="border-4 border-double border-white bg-blue-800 rounded-lg p-3 shadow-xl relative animate-in fade-in duration-300">
            <div className="absolute top-2 right-2 opacity-10 pointer-events-none"><Crown size={80} /></div>
            <div className="flex items-start gap-4 relative z-10">
                <div className="text-5xl bg-blue-900 p-2 rounded border-2 border-white shadow-inner">
                    {user.avatar || '🙂'}
                </div>
                <div className="flex-1 space-y-1">
                    <div className="flex justify-between items-baseline border-b border-blue-600 pb-1">
                        <span className="text-lg font-bold text-yellow-300 tracking-widest">{user.name}</span>
                        <span className="text-sm text-cyan-200">{user.job_class} Lv.{user.level}</span>
                    </div>
                    <div className="grid grid-cols-[30px_1fr] items-center text-sm gap-2">
                        <span className="font-bold text-red-300">HP</span>
                        <div className="w-full bg-gray-900 h-3 rounded border border-gray-600 overflow-hidden">
                            <div className="bg-gradient-to-r from-green-500 to-green-400 h-full" style={{ width: '100%' }}></div>
                        </div>

                        <span className="font-bold text-orange-300">EXP</span>
                        <div className="w-full bg-gray-900 h-3 rounded border border-gray-600 overflow-hidden relative">
                            <div className="bg-gradient-to-r from-orange-500 to-yellow-400 h-full transition-all duration-700"
                                style={{ width: `${expPercentage}%` }}></div>
                            <div className="absolute inset-0 text-[8px] flex items-center justify-center text-white/80 font-bold">
                                あと {expRemaining}
                            </div>
                        </div>

                        <span className="font-bold text-yellow-300">G</span>
                        <div className="text-right font-bold text-yellow-300">{(user.gold || 0).toLocaleString()} G</div>

                        <span className="font-bold text-yellow-500">🏅</span>
                        <div className="text-right font-bold text-yellow-500">{(user.medal_count || 0)} 枚</div>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default UserStatusCard;