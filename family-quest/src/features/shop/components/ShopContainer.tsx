// src/features/shop/components/ShopContainer.tsx
import React, { useState } from 'react';
import RewardList from './RewardList'; // default export か named export か確認してください
import { InventoryList } from './InventoryList';
import { Reward } from '@/types';

// App.tsx から受け取るデータの型定義
type Props = {
    userId: string;
    rewards: Reward[];
    userGold: number;
    onBuy: (reward: Reward) => void;
};

export const ShopContainer: React.FC<Props> = ({ userId, rewards, userGold, onBuy }) => {
    // デフォルトは「お店」タブ
    const [activeTab, setActiveTab] = useState<'shop' | 'inventory'>('shop');

    return (
        <div className="space-y-4">
            {/* 内部タブ切り替えボタン */}
            <div className="flex p-1 bg-slate-200 rounded-xl">
                <button
                    onClick={() => setActiveTab('shop')}
                    className={`flex-1 py-2 text-sm font-bold rounded-lg transition-all duration-200 ${activeTab === 'shop'
                            ? 'bg-white text-blue-600 shadow-sm'
                            : 'text-slate-500 hover:text-slate-700'
                        }`}
                >
                    🏪 お店
                </button>
                <button
                    onClick={() => setActiveTab('inventory')}
                    className={`flex-1 py-2 text-sm font-bold rounded-lg transition-all duration-200 ${activeTab === 'inventory'
                            ? 'bg-white text-green-600 shadow-sm'
                            : 'text-slate-500 hover:text-slate-700'
                        }`}
                >
                    🎒 もちもの
                </button>
            </div>

            {/* コンテンツエリア */}
            <div className="min-h-[300px]">
                {activeTab === 'shop' ? (
                    <div className="animate-fade-in">
                        <RewardList
                            rewards={rewards}
                            userGold={userGold}
                            onBuy={onBuy}
                        />
                    </div>
                ) : (
                    <div className="animate-fade-in">
                        <div className="bg-green-50 p-3 rounded-lg mb-2 text-center text-xs text-green-600">
                            つかうときは「つかう！」ボタンを押してね
                        </div>
                        <InventoryList userId={userId} />
                    </div>
                )}
            </div>
        </div>
    );
};