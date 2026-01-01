import React from 'react';
import { Crown, Camera } from 'lucide-react'; // Cameraアイコン追加

const UserStatusCard = ({ user, onAvatarClick }) => { // onAvatarClick プロップス追加
    if (!user) return null;

    const expPercentage = ((user.exp || 0) / (user.nextLevelExp || 100)) * 100;
    const expRemaining = (user.nextLevelExp || 100) - (user.exp || 0);

    // アバターが画像URLか絵文字かを判定 (/uploads/ または http で始まる場合は画像)
    const isImageAvatar = user.avatar && (user.avatar.startsWith('/uploads/') || user.avatar.startsWith('http'));

    return (
        <div className="border-4 border-double border-white bg-blue-800 rounded-lg p-3 shadow-xl relative animate-in fade-in duration-300">
            <div className="absolute top-2 right-2 opacity-10 pointer-events-none"><Crown size={80} /></div>
            <div className="flex items-start gap-4 relative z-10">

                {/* アバター部分: クリック可能に変更 */}
                <div
                    className="relative group cursor-pointer"
                    onClick={() => onAvatarClick && onAvatarClick(user)}
                >
                    <div className="w-16 h-16 bg-blue-900 rounded border-2 border-white shadow-inner overflow-hidden flex items-center justify-center">
                        {isImageAvatar ? (
                            <img src={user.avatar} alt={user.name} className="w-full h-full object-cover" />
                        ) : (
                            <span className="text-4xl">{user.avatar || '🙂'}</span>
                        )}
                    </div>
                    {/* ホバー/クリック等のヒントアイコン */}
                    <div className="absolute -bottom-2 -right-2 bg-white text-blue-900 rounded-full p-1 shadow border border-blue-900">
                        <Camera size={12} />
                    </div>
                </div>

                <div className="flex-1 space-y-1">
                    {/* ... (残りのコードは変更なし) ... */}
                    <div className="flex justify-between items-baseline border-b border-blue-600 pb-1">
                        <span className="text-lg font-bold text-yellow-300 tracking-widest">{user.name}</span>
                        <span className="text-sm text-cyan-200">{user.job_class} Lv.{user.level}</span>
                    </div>
                    {/* ... (HP/EXPバーなどはそのまま) ... */}
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