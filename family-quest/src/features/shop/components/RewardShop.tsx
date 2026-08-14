import React from 'react';
import { Coins } from 'lucide-react';
import { Reward, User } from '@/types';
import { CountUp } from '@/components/ui/CountUp';
import RewardList from './RewardList';
import { InventoryList } from './InventoryList';

interface RewardShopProps {
  rewards: Reward[];
  currentUser: User;
  onBuy: (reward: Reward) => void;
}

// 「ごほうび」画面: 所持ゴールド表示 → 購入可能な報酬一覧 → 所持品(使用ボタン付き) の順で構成する。
// RewardList(購入)とInventoryList(所持品・使用申請)を1画面にまとめ、「もちもの」タブは廃止する。
const RewardShop: React.FC<RewardShopProps> = ({ rewards, currentUser, onBuy }) => {
  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300 pb-20">
      <div className="flex items-center justify-center gap-2 bg-blue-900/60 border border-yellow-500/50 rounded-lg py-2">
        <Coins size={18} className="text-yellow-300" />
        <span className="text-yellow-300 font-bold text-sm">所持ゴールド</span>
        <span className="text-yellow-300 font-mono font-bold text-lg">
          <CountUp value={currentUser.gold || 0} suffix=" G" />
        </span>
      </div>

      <RewardList
        rewards={rewards}
        userGold={currentUser.gold}
        onBuy={onBuy}
        currentUser={currentUser}
      />

      <div>
        <div className="text-center border-b border-gray-600 pb-1 mb-2 text-yellow-300 text-sm font-bold">
          -- もちもの --
        </div>
        <InventoryList userId={currentUser.user_id} />
      </div>
    </div>
  );
};

export default RewardShop;
