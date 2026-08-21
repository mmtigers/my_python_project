import React from 'react';
import { Reward, User } from '@/types';
import RewardList from './RewardList';

interface RewardShopProps {
  rewards: Reward[];
  currentUser: User;
  onBuy: (reward: Reward) => void;
}

// 「ごほうび」画面: 購入可能な報酬一覧のみを表示する。
// 所持ゴールドはステータスカードに既に表示されているため重複表示しない。
// もちもの(所持品)は独立した別タブ(InventoryList)に戻したため、ここでは扱わない。
const RewardShop: React.FC<RewardShopProps> = ({ rewards, currentUser, onBuy }) => {
  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300 pb-20">
      <RewardList
        rewards={rewards}
        userGold={currentUser.gold}
        onBuy={onBuy}
        currentUser={currentUser}
      />
    </div>
  );
};

export default RewardShop;
