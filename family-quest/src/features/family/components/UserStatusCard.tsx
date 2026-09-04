import React from 'react';
import { User } from '@/types';
import { CountUp } from '@/components/ui/CountUp';
import { isSameOriginAvatarPath } from '../../../lib/utils';

interface UserStatusCardProps {
    user: User;
    onAvatarClick: (user: User) => void;
}

const UserStatusCard: React.FC<UserStatusCardProps> = ({ user, onAvatarClick }) => {
    if (!user) return null;

    return (
        <div className="border-4 border-double border-white bg-blue-800 rounded-lg p-2 shadow-xl relative animate-in fade-in duration-300">
            <div className="flex items-center gap-3 relative z-10">
                {/* アバター */}
                <div
                    onClick={() => onAvatarClick(user)}
                    className="text-4xl bg-blue-900 p-1 rounded border-2 border-white shadow-inner cursor-pointer hover:brightness-110 active:scale-95 transition-all w-[52px] h-[52px] flex items-center justify-center overflow-hidden flex-shrink-0"
                >
                    {/* ★バグ修正: user.avatar はアップロード画像のパス('/uploads/...')の場合と、
                        未設定時の絵文字デフォルト値の場合がある。パス以外を<img src>に渡すと
                        壊れた画像アイコンになるため、Header.tsxと同様にパス形式かどうかを判定する */}
                    {isSameOriginAvatarPath(user.avatar) ? (
                        <img src={user.avatar} alt="avatar" className="w-full h-full object-cover" />
                    ) : (
                        user.avatar || '🙂'
                    )}
                </div>

                {/* ステータス詳細 */}
                <div className="flex-1 min-w-0 space-y-1">
                    <div className="flex justify-between items-baseline border-b border-blue-600 pb-1">
                        <span className="text-base font-bold text-yellow-300 tracking-widest truncate">{user.name}</span>
                        <span className="text-xs text-cyan-200 whitespace-nowrap">{user.job_class || '冒険者'} Lv.{user.level}</span>
                    </div>

                    {/* ゴールドとメダルは1行にまとめる(カードの縦幅を抑えるため) */}
                    <div className="flex items-center justify-between text-sm gap-2">
                        <div className="flex items-center gap-1 font-bold text-yellow-300 tabular-nums">
                            <span>G</span>
                            <CountUp value={user.gold || 0} suffix=" G" />
                        </div>
                        <div className="flex items-center gap-1 font-bold text-yellow-500 tabular-nums">
                            <span>🏅</span>
                            <CountUp value={user.medal_count || 0} suffix=" 枚" />
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default UserStatusCard;