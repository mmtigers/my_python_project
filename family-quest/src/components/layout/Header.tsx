import React from 'react';
import { User } from '@/types';
import { Scroll, Settings, Home } from 'lucide-react';
import { isSameOriginAvatarPath } from '../../lib/utils';

interface HeaderProps {
    users: User[];
    currentUserIdx: number;
    viewMode: 'user' | 'familyLog';
    onUserSwitch: (idx: number) => void;
    onLogSwitch: () => void;
    onSettingsClick: () => void;
    // 横画面(4人常時表示レイアウト)では、各ユーザーのアバターは既にメイン画面の
    // パネルに常時表示されているため、ヘッダー側のユーザー切替行は冗長になる。
    // true の場合はユーザー切替行を省略し、タイトルと記録ボタンのみを表示する。
    hideUserSwitcher?: boolean;
    // 縦画面ではフッターナビ(BottomNav)に「記録」タブが統合されたため、
    // ヘッダー側の記録ボタンは二重導線になる。true の場合は非表示にする。
    hideLogSwitcher?: boolean;
    // ★バグ修正: 横画面(4人並び)で記録画面を表示中は、ユーザー切替行の代わりに
    // 単一の「ホームに戻る」ボタンを表示する。以前はユーザー切替行をそのまま
    // 出していたため、4人分のボタンが並んでしまい「ホームに戻る」という意図が
    // 伝わらなかった。
    showBackToMain?: boolean;
    onBackToMain?: () => void;
}

const Header: React.FC<HeaderProps> = ({
    users,
    currentUserIdx,
    viewMode,
    onUserSwitch,
    onLogSwitch,
    onSettingsClick,
    hideUserSwitcher,
    hideLogSwitcher,
    showBackToMain,
    onBackToMain,
}) => {
    return (
        <header className="bg-gradient-to-b from-gray-900 to-black border-b-4 border-gray-800 pb-4 shadow-2xl relative z-20">

            {/* 表示せっていボタン */}
            <div className="absolute top-2 right-2 flex gap-1 z-30">
                <button
                    onClick={onSettingsClick}
                    aria-label="表示せってい"
                    className="w-10 h-10 min-w-[44px] min-h-[44px] flex items-center justify-center rounded-full bg-gray-800/80 border border-gray-600 text-gray-300 hover:text-white hover:bg-gray-700 transition-colors"
                >
                    <Settings size={18} />
                </button>
            </div>

            {/* Title Area */}
            <div className="pt-4 pb-2 text-center relative">
                <h1 className="text-2xl font-black text-yellow-500 tracking-widest drop-shadow-[0_2px_2px_rgba(0,0,0,0.8)]" style={{ fontFamily: '"Press Start 2P", cursive, sans-serif' }}>
                    FAMILY QUEST
                </h1>
                <p className="text-[10px] text-gray-400 font-mono">我が家の冒険譚</p>
            </div>

            {/* Unified Navigation Area (Users + Log) */}
            <div className="flex flex-wrap justify-center items-end gap-2 sm:gap-4 px-2 mt-2">

                {/* 1. ホームボタン(横画面のみ)。トップ画面でも表示して統一感を持たせる
                    (トップ画面では押しても画面遷移は起きない: 既にメイン画面のため)。
                    ★バグ修正: 以前はスタイルが常に「選択中」固定だったため、記録画面に
                    遷移したあともホームボタンだけフォーカスされたままに見えていた。
                    他のボタン同様、viewMode に応じて選択中/非選択を切り替える */}
                {showBackToMain && (
                    <button
                        onClick={onBackToMain}
                        className={`relative transition-all duration-300 flex flex-col items-center group p-1 ${viewMode === 'user' ? 'scale-110 -translate-y-1 z-10' : 'scale-95 opacity-60 hover:opacity-100 hover:scale-100'
                            }`}
                    >
                        <div className={`w-16 h-16 sm:w-20 sm:h-20 rounded-full border-4 shadow-lg flex items-center justify-center relative transition-colors ${viewMode === 'user'
                            ? 'border-yellow-400 ring-4 ring-yellow-500/30 bg-gray-800 text-yellow-400'
                            : 'border-gray-600 bg-gray-900 text-gray-400'
                            }`}>
                            <Home size={32} />
                        </div>
                        <div className={`mt-2 px-3 py-1 rounded-full text-[10px] sm:text-xs font-bold shadow-md transition-colors whitespace-nowrap ${viewMode === 'user'
                            ? 'bg-yellow-600 text-white border border-yellow-300 transform scale-110'
                            : 'bg-gray-800 text-gray-400 border border-gray-600'
                            }`}>
                            ホーム
                        </div>
                        {viewMode === 'user' && (
                            <div className="absolute -bottom-2 text-yellow-400 animate-bounce text-xs">▲</div>
                        )}
                    </button>
                )}

                {/* 2. Users */}
                {!hideUserSwitcher && users.map((user, idx) => {
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
                                {isSameOriginAvatarPath(user.avatar) ? (
                                    <img src={user.avatar} alt={user.name} className="w-full h-full object-cover" />
                                ) : (
                                    <div className="w-full h-full flex items-center justify-center text-3xl">
                                        {user.avatar || user.icon || '🙂'}
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
                {(!hideUserSwitcher || showBackToMain) && !hideLogSwitcher && (
                    <div className="w-px h-12 bg-gray-700 mx-1 self-center hidden sm:block"></div>
                )}

                {/* 3. Log Button (縦画面ではフッターナビに統合済みのため非表示) */}
                {!hideLogSwitcher && (
                    <button
                        onClick={onLogSwitch}
                        className={`relative transition-all duration-300 flex flex-col items-center group p-1 ${viewMode === 'familyLog' ? 'scale-110 -translate-y-1 z-10' : 'scale-95 opacity-60 hover:opacity-100 hover:scale-100'
                            }`}
                    >
                        <div className={`
                w-16 h-16 sm:w-20 sm:h-20 rounded-full border-4 shadow-lg flex items-center justify-center relative transition-colors
                ${viewMode === 'familyLog'
                                ? 'border-purple-400 ring-4 ring-purple-500/30 bg-gray-800 text-purple-400'
                                : 'border-gray-600 bg-gray-900 text-gray-400'}
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
                )}

            </div>
        </header>
    );
};

export default Header;
