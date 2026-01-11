import { Coins, BookOpen, Users } from 'lucide-react';
import { User } from '@/types'; // 型定義をインポート

interface HeaderProps {
    users: User[];
    currentUserIdx: number;
    viewMode: string;
    onUserSwitch: (index: number) => void;
    onPartySwitch: () => void;
    onLogSwitch: () => void;
}

export default function Header({
    users,
    currentUserIdx,
    viewMode,
    onUserSwitch,
    onPartySwitch,
    onLogSwitch
}: HeaderProps) {
    const currentUser = users[currentUserIdx];

    return (
        <div className="bg-blue-900 border-b-4 border-white p-2 sticky top-0 z-10 shadow-lg">
            <div className="flex gap-1 mb-2 overflow-x-auto no-scrollbar">
                {users.map((u, idx) => (
                    <button
                        key={u.user_id || idx}
                        onClick={() => onUserSwitch(idx)}
                        className={`flex-1 min-w-[80px] px-2 py-1.5 border-2 rounded text-sm font-bold transition-all whitespace-nowrap ${viewMode === 'user' && currentUserIdx === idx
                                ? 'bg-yellow-500 border-white text-black translate-y-0.5'
                                : 'bg-blue-800 border-gray-400 text-gray-300'
                            }`}
                    >
                        {u.name}
                    </button>
                ))}

                <button
                    onClick={onPartySwitch}
                    className={`flex-1 min-w-[80px] px-2 py-1.5 border-2 rounded text-sm font-bold transition-all whitespace-nowrap flex items-center justify-center gap-1 ${viewMode === 'party'
                            ? 'bg-purple-600 border-white text-white translate-y-0.5'
                            : 'bg-blue-800 border-gray-400 text-gray-300'
                        }`}
                >
                    <Users size={14} />
                    パーティー
                </button>

                <button
                    onClick={onLogSwitch}
                    className={`flex-1 min-w-[80px] px-2 py-1.5 border-2 rounded text-sm font-bold transition-all whitespace-nowrap flex items-center justify-center gap-1 ${viewMode === 'familyLog'
                            ? 'bg-green-600 border-white text-white translate-y-0.5'
                            : 'bg-blue-800 border-gray-400 text-gray-300'
                        }`}
                >
                    <BookOpen size={14} />
                    記録
                </button>
            </div>

            {viewMode === 'user' && currentUser && (
                <div className="flex justify-end">
                    <div className="flex items-center gap-2 bg-black/50 px-3 py-1 rounded border border-yellow-600">
                        <Coins className="text-yellow-400" size={16} />
                        <div className="text-xl font-bold text-yellow-300 tabular-nums">
                            {(currentUser.gold ?? 0).toLocaleString()}
                        </div>
                        <div className="text-[10px] text-yellow-500">G</div>
                    </div>
                </div>
            )}

            <style>{`
        .no-scrollbar::-webkit-scrollbar { display: none; }
        .no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
      `}</style>
        </div>
    );
}