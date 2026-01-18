import React from 'react';
import { User } from '@/types';
import { Users, Scroll } from 'lucide-react';

interface HeaderProps {
    users: User[];
    currentUserIdx: number;
    viewMode: 'user' | 'party' | 'familyLog';
    onUserSwitch: (idx: number) => void;
    onPartySwitch: () => void;
    onLogSwitch: () => void;
}

const Header: React.FC<HeaderProps> = ({
    users,
    currentUserIdx,
    viewMode,
    onUserSwitch,
    onPartySwitch,
    onLogSwitch,
}) => {
    return (
        <header className="bg-gradient-to-b from-gray-900 to-black border-b-4 border-gray-800 pb-4 shadow-2xl relative z-20">

            {/* Title Area (隠しボタン用エリア) */}
            <div className="pt-4 pb-2 text-center relative pointer-events-none">
                <h1 className="text-2xl font-black text-yellow-500 tracking-widest drop-shadow-[0_2px_2px_rgba(0,0,0,0.8)]" style={{ fontFamily: '"Press Start 2P", cursive, sans-serif' }}>
                    FAMILY QUEST
                </h1>
                <p className="text-[10px] text-gray-500 font-mono">我が家の冒険譚</p>
            </div>

            {/* Unified Navigation Area (Users + Party + Log) */}
            <div className="flex flex-wrap justify-center items-end gap-2 sm:gap-4 px-2 mt-2">

                {/* 1. Users */}
                {users.map((user, idx) => {
                    const isActive = viewMode === 'user' && currentUserIdx === idx;
                    return (
                        <button
                            key={user.user_id}
                            onClick={() => onUserSwitch(idx)}
                            className={`relative transition-all duration-300 flex flex-col items-center group p-1 ${isActive ? 'scale-110 -translate-y-1 z-10' : 'scale-95 opacity-60 hover:opacity-100 hover:scale-100'
                                }`}
                        >
                            {/* Avatar Circle */}
                            <div className={`
                w-16 h-16 sm:w-20 sm:h-20 rounded-full border-4 shadow-lg overflow-hidden relative transition-colors
                ${isActive
                                    ? 'border-yellow-400 ring-4 ring-yellow-500/30 bg-gray-800'
                                    : 'border-gray-600 bg-gray-900'}
              `}>
                                {user.avatar ? (
                                    <img src={user.avatar} alt={user.name} className="w-full h-full object-cover" />
                                ) : (
                                    <div className="w-full h-full flex items-center justify-center text-3xl">
                                        {user.icon || '🙂'}
                                    </div>
                                )}
                            </div>

                            {/* Name Badge */}
                            <div className={`
                mt-2 px-3 py-1 rounded-full text-[10px] sm:text-xs font-bold shadow-md transition-colors whitespace-nowrap
                ${isActive
                                    ? 'bg-yellow-600 text-white border border-yellow-300 transform scale-110'
                                    : 'bg-gray-800 text-gray-400 border border-gray-600'}
              `}>
                                {user.name}
                            </div>

                            {isActive && (
                                <div className="absolute -bottom-2 text-yellow-400 animate-bounce text-xs">▲</div>
                            )}
                        </button>
                    );
                })}

                {/* Divider (PCのみ表示) */}
                <div className="w-px h-12 bg-gray-700 mx-1 self-center hidden sm:block"></div>

                {/* 2. Party Button */}
                <button
                    onClick={onPartySwitch}
                    className={`relative transition-all duration-300 flex flex-col items-center group p-1 ${viewMode === 'party' ? 'scale-110 -translate-y-1 z-10' : 'scale-95 opacity-60 hover:opacity-100 hover:scale-100'
                        }`}
                >
                    <div className={`
            w-16 h-16 sm:w-20 sm:h-20 rounded-full border-4 shadow-lg flex items-center justify-center relative transition-colors
            ${viewMode === 'party'
                            ? 'border-blue-400 ring-4 ring-blue-500/30 bg-gray-800 text-blue-400'
                            : 'border-gray-600 bg-gray-900 text-gray-500'}
          `}>
                        <Users size={32} />
                    </div>
                    <div className={`
            mt-2 px-3 py-1 rounded-full text-[10px] sm:text-xs font-bold shadow-md transition-colors whitespace-nowrap
            ${viewMode === 'party'
                            ? 'bg-blue-600 text-white border border-blue-300 transform scale-110'
                            : 'bg-gray-800 text-gray-400 border border-gray-600'}
          `}>
                        パーティ
                    </div>
                    {viewMode === 'party' && (
                        <div className="absolute -bottom-2 text-blue-400 animate-bounce text-xs">▲</div>
                    )}
                </button>

                {/* 3. Log Button */}
                <button
                    onClick={onLogSwitch}
                    className={`relative transition-all duration-300 flex flex-col items-center group p-1 ${viewMode === 'familyLog' ? 'scale-110 -translate-y-1 z-10' : 'scale-95 opacity-60 hover:opacity-100 hover:scale-100'
                        }`}
                >
                    <div className={`
            w-16 h-16 sm:w-20 sm:h-20 rounded-full border-4 shadow-lg flex items-center justify-center relative transition-colors
            ${viewMode === 'familyLog'
                            ? 'border-purple-400 ring-4 ring-purple-500/30 bg-gray-800 text-purple-400'
                            : 'border-gray-600 bg-gray-900 text-gray-500'}
          `}>
                        <Scroll size={32} />
                    </div>
                    <div className={`
            mt-2 px-3 py-1 rounded-full text-[10px] sm:text-xs font-bold shadow-md transition-colors whitespace-nowrap
            ${viewMode === 'familyLog'
                            ? 'bg-purple-600 text-white border border-purple-300 transform scale-110'
                            : 'bg-gray-800 text-gray-400 border border-gray-600'}
          `}>
                        記録
                    </div>
                    {viewMode === 'familyLog' && (
                        <div className="absolute -bottom-2 text-purple-400 animate-bounce text-xs">▲</div>
                    )}
                </button>

            </div>
        </header>
    );
};

export default Header;