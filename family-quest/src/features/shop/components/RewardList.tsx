import React from 'react';
import { ShoppingBag } from 'lucide-react';
import { Reward } from '@/types';
import { Card } from '@/components/ui/Card';

interface RewardListProps {
  rewards: Reward[];
  userGold: number;
  onBuy: (reward: Reward) => void;
}

const RewardList: React.FC<RewardListProps> = ({ rewards, userGold, onBuy }) => {
  return (
    <div className="space-y-2 animate-in fade-in slide-in-from-bottom-2 duration-300">
      <div className="text-center border-b border-gray-600 pb-1 mb-2 text-yellow-300 text-sm font-bold flex items-center justify-center gap-2">
        <ShoppingBag size={16} />
        <span>-- 商品一覧 --</span>
      </div>

      {rewards.length === 0 && (
        <div className="text-center text-gray-500 py-4 text-xs">商品が入荷待ちです...</div>
      )}

      {rewards.map((reward, index) => {
        const cost = reward.cost_gold || reward.cost || 0;
        const canAfford = userGold >= cost;
        const rId = reward.reward_id || reward.id || index;

        return (
          <Card
            key={rId}
            onClick={() => canAfford && onBuy(reward)}
            className={`
              flex justify-between items-center p-2 transition-all select-none
              ${canAfford
                ? 'border-white bg-blue-900/80 hover:bg-blue-800 hover:border-yellow-200 cursor-pointer active:scale-[0.98]'
                : 'border-gray-700 bg-gray-900/50 opacity-60 cursor-not-allowed grayscale'}
            `}
          >
            <div className="flex items-center gap-3">
              <span className="text-2xl filter drop-shadow-lg">{reward.icon || reward.icon_key || '🎁'}</span>
              <div>
                <div className={`font-bold ${canAfford ? 'text-white' : 'text-gray-400'}`}>
                  {reward.title}
                </div>
                <div className="text-xs text-gray-400 uppercase tracking-wider">
                  {reward.category || 'General'}
                </div>
              </div>
            </div>

            <div className={`flex items-center gap-1 font-bold ${canAfford ? 'text-yellow-300' : 'text-red-400'}`}>
              {cost.toLocaleString()} <span className="text-[10px]">G</span>
              {!canAfford && <span className="text-[10px] ml-1">(不足)</span>}
            </div>
          </Card>
        );
      })}
    </div>
  );
};

export default RewardList;