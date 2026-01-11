import React from 'react';
import { Shield, Sword } from 'lucide-react';
import { User, Equipment } from '@/types';

interface EquipmentShopProps {
    equipments: Equipment[];
    ownedEquipments: any[];
    currentUser: User;
    onBuy: (item: Equipment) => void;
    onEquip: (item: Equipment) => void;
}

const EquipmentShop: React.FC<EquipmentShopProps> = ({
    equipments,
    ownedEquipments,
    currentUser,
    onBuy,
    onEquip
}) => {
    // 自分の持っているアイテム情報を取得するヘルパー
    const getOwnedStatus = (itemId: number) => {
        return ownedEquipments.find(
            (oe: any) => oe.equipment_id === itemId && oe.user_id === currentUser.user_id
        );
    };

    // セクションごとにレンダリング
    const renderSection = (title: string, type: string, icon: React.ReactNode) => {
        const items = equipments.filter(e => e.type === type);
        if (items.length === 0) return null;

        return (
            <div className="mb-4">
                <div className="text-center border-b border-gray-600 pb-1 mb-2 text-cyan-300 text-sm font-bold flex items-center justify-center gap-2">
                    {icon}
                    <span>-- {title} --</span>
                </div>

                <div className="space-y-2">
                    {items.map(item => {
                        const itemId = Number(item.equipment_id || item.id);
                        const owned = getOwnedStatus(itemId);
                        const isEquipped = owned?.is_equipped === 1;
                        const canAfford = (currentUser.gold || 0) >= item.cost;


                        return (
                            <div
                                key={itemId}
                                className={`
                  border p-2 rounded flex justify-between items-center transition-all select-none
                  ${isEquipped
                                        ? 'border-yellow-400 bg-yellow-900/40'
                                        : 'border-gray-700 bg-gray-900/50'}
                `}
                            >
                                {/* 左側: アイコンと情報 */}
                                <div className="flex items-center gap-3">
                                    <div className="relative">
                                        <span className="text-2xl">{item.icon || (type === 'weapon' ? '🗡️' : '🛡️')}</span>
                                        {isEquipped && (
                                            <span className="absolute -top-1 -right-1 bg-yellow-500 text-black text-[8px] font-bold px-1 rounded-full animate-pulse">E</span>
                                        )}
                                    </div>
                                    <div>
                                        <div className={`font-bold ${isEquipped ? 'text-yellow-200' : 'text-gray-300'}`}>
                                            {item.name}
                                        </div>
                                        <div className="text-xs text-gray-500">
                                            攻撃/防御 +{item.power}
                                        </div>
                                    </div>
                                </div>

                                {/* 右側: アクションボタン */}
                                <div>
                                    {owned ? (
                                        isEquipped ? (
                                            <span className="text-xs text-yellow-500 font-bold border border-yellow-600 px-2 py-1 rounded bg-black/50">
                                                装備中
                                            </span>
                                        ) : (
                                            <button
                                                onClick={() => onEquip(item)}
                                                className="text-xs bg-blue-700 hover:bg-blue-600 text-white px-3 py-1.5 rounded border border-blue-500 shadow-lg active:scale-95"
                                            >
                                                装備する
                                            </button>
                                        )
                                    ) : (
                                        <button
                                            onClick={() => canAfford && onBuy(item)}
                                            disabled={!canAfford}
                                            className={`
                        text-xs px-3 py-1.5 rounded border shadow-lg active:scale-95 flex flex-col items-center min-w-[60px]
                        ${canAfford
                                                    ? 'bg-red-800 hover:bg-red-700 text-white border-red-500'
                                                    : 'bg-gray-800 text-gray-500 border-gray-700 cursor-not-allowed opacity-50'}
                      `}
                                        >
                                            <span>購入</span>
                                            <span className="text-[10px]">{item.cost} G</span>
                                        </button>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>
        );
    };

    if (!equipments || equipments.length === 0) {
        return <div className="text-center text-gray-500 py-10">商品が入荷待ちです...</div>;
    }

    return (
        <div className="animate-in fade-in slide-in-from-bottom-2 duration-300 pb-4">
            {renderSection("武器", "weapon", <Sword size={16} />)}
            {renderSection("防具", "armor", <Shield size={16} />)}
        </div>
    );
};

export default EquipmentShop;