import React from 'react';
import { Crown } from 'lucide-react';
import { User } from '@/types';
import { CountUp } from '@/components/ui/CountUp';

interface UserStatusCardProps {
    user: User;
    onAvatarClick: (user: User) => void;
}

const UserStatusCard: React.FC<UserStatusCardProps> = ({ user, onAvatarClick }) => {
    if (!user) return null;

    // 簡易計算: 次のレベルまで = (Lv+1)*100 とする (APIデータがあればそれを使う)
    const nextLevelExp = (user.level + 1) * 100;
    const currentExp = user.exp || 0;

    // 進捗率と残りEXP
    const expPercentage = Math.min(100, (currentExp / nextLevelExp) * 100);
    const expRemaining = nextLevelExp - currentExp;

    // HP計算 (簡易ロジック: Lv * 10 + 50)
    // APIに hp / max_hp があればそれを使う
    const maxHp = (user.level * 10) + 50;
    const currentHp = maxHp; // とりあえず満タン表示
    const hpPercentage = 100;

    return (
        <div className="border-4 border-double border-white bg-blue-800 rounded-lg p-3 shadow-xl relative animate-in fade-in duration-300">
            <div className="absolute top-2 right-2 opacity-10 pointer-events-none">
                <Crown size={80} />
            </div>

            <div className="flex items-start gap-4 relative z-10">
                {/* アバター */}
                <div
                    onClick={() => onAvatarClick(user)}
                    className="text-5xl bg-blue-900 p-2 rounded border-2 border-white shadow-inner cursor-pointer hover:brightness-110 active:scale-95 transition-all w-[70px] h-[70px] flex items-center justify-center overflow-hidden"
                >
                    {user.avatar ? (
                        <img src={user.avatar} alt="avatar" className="w-full h-full object-cover" />
                    ) : (
                        user.icon || '🙂'
                    )}
                </div>

                {/* ステータス詳細 */}
                <div className="flex-1 space-y-1">
                    <div className="flex justify-between items-baseline border-b border-blue-600 pb-1">
                        <span className="text-lg font-bold text-yellow-300 tracking-widest truncate">{user.name}</span>
                        <span className="text-sm text-cyan-200 whitespace-nowrap">{user.job_class || '冒険者'} Lv.{user.level}</span>
                    </div>

                    <div className="grid grid-cols-[30px_1fr] items-center text-sm gap-2">

                        {/* HPバー */}
                        <span className="font-bold text-red-300">HP</span>
                        <div className="w-full bg-gray-900 h-3 rounded border border-gray-600 overflow-hidden relative">
                            <div
                                className="bg-gradient-to-r from-green-500 to-green-400 h-full transition-all duration-700"
                                style={{ width: `${hpPercentage}%` }}
                            />
                            <div className="absolute inset-0 text-[8px] flex items-center justify-center text-white/80 font-bold leading-none">
                                <span className="flex gap-0.5">
                                    <CountUp value={currentHp} /> / {maxHp}
                                </span>
                            </div>
                        </div>

                        {/* EXPバー */}
                        <span className="font-bold text-orange-300">EXP</span>
                        <div className="w-full bg-gray-900 h-3 rounded border border-gray-600 overflow-hidden relative">
                            <div
                                className="bg-gradient-to-r from-orange-500 to-yellow-400 h-full transition-all duration-700"
                                style={{ width: `${expPercentage}%` }}
                            />
                            <div className="absolute inset-0 text-[8px] flex items-center justify-center text-white/80 font-bold leading-none">
                                あと {expRemaining}
                            </div>
                        </div>

                        {/* ゴールド */}
                        <span className="font-bold text-yellow-300">G</span>
                        <div className="text-right font-bold text-yellow-300 tabular-nums">
                            <CountUp value={user.gold || 0} suffix=" G" />
                        </div>

                        {/* メダル */}
                        <span className="font-bold text-yellow-500">🏅</span>
                        <div className="text-right font-bold text-yellow-500 tabular-nums">
                            <CountUp value={user.medal_count || 0} suffix=" 枚" />
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default UserStatusCard;