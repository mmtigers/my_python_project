import React from 'react';
import { Sword, ShoppingBag, Package, Scroll } from 'lucide-react';

export type BottomNavTab = 'quest' | 'shop' | 'inventory' | 'familyLog';

interface Props {
    active: BottomNavTab;
    onChange: (tab: BottomNavTab) => void;
}

// ★バグ修正: ごほうび画面へのもちもの統合をやめ、「クエスト/ごほうび/もちもの/記録」の
// 4タブ構成に戻す。クエスト/ごほうび/もちものはアイコンのみ表示(iconOnly)にする。
const TABS: { key: BottomNavTab; label: string; icon: React.ElementType; activeColor: string; iconOnly?: boolean }[] = [
    { key: 'quest', label: 'クエスト', icon: Sword, activeColor: 'text-blue-400', iconOnly: true },
    { key: 'shop', label: 'ごほうび', icon: ShoppingBag, activeColor: 'text-orange-400', iconOnly: true },
    { key: 'inventory', label: 'もちもの', icon: Package, activeColor: 'text-green-400', iconOnly: true },
    { key: 'familyLog', label: '記録', icon: Scroll, activeColor: 'text-purple-400', iconOnly: true },
];

// 角度⑦: 縦画面での「上部stickyタブ(クエスト/ごほうび)」+「ヘッダーの記録ボタン」という
// 二重のナビゲーション構造を廃止し、フッター1本の4タブに統一する。
const BottomNav: React.FC<Props> = ({ active, onChange }) => {
    return (
        <nav
            className="fixed bottom-0 inset-x-0 z-30 bg-gray-900/95 backdrop-blur border-t-2 border-gray-700 flex shadow-[0_-4px_12px_rgba(0,0,0,0.4)]"
            style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
        >
            {TABS.map(tab => {
                const Icon = tab.icon;
                const isActive = active === tab.key;
                return (
                    <button
                        key={tab.key}
                        onClick={() => onChange(tab.key)}
                        aria-label={tab.label}
                        className={`flex-1 min-h-[56px] flex flex-col items-center justify-center gap-0.5 transition-all ${isActive ? tab.activeColor : 'text-gray-400'}`}
                    >
                        <Icon size={22} className={`transition-transform ${isActive ? 'scale-110' : ''}`} />
                        {!tab.iconOnly && <span className="text-[10px] font-bold">{tab.label}</span>}
                    </button>
                );
            })}
        </nav>
    );
};

export default BottomNav;
