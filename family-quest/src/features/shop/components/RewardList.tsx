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

        // ★追加: 説明文の優先順位ロジック
        const displayText = reward.description || reward.desc || reward.category || 'General';

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
            {/* ★変更: 左側のアイコンとテキストエリア (overflow-hiddenではみ出し防止) */}
            <div className="flex items-center gap-3 overflow-hidden">
              <span className="text-2xl filter drop-shadow-lg flex-shrink-0">
                {reward.icon || reward.icon_key || '🎁'}
              </span>

              {/* ★変更: テキストエリア (min-w-0で縮小を許可) */}
              <div className="min-w-0">
                <div className={`font-bold truncate ${canAfford ? 'text-white' : 'text-gray-400'}`}>
                  {reward.title}
                </div>
                {/* ★変更: uppercase削除、2行まで表示、文字サイズ調整 */}
                <div className="text-[10px] text-gray-300 leading-tight line-clamp-2">
                  {displayText}
                </div>
              </div>
            </div>

            {/* ★変更: 右側の価格エリア (flex-shrink-0で価格が潰れるのを防ぐ) */}
            <div className={`flex-shrink-0 flex items-center gap-1 font-bold pl-2 ${canAfford ? 'text-yellow-300' : 'text-red-400'}`}>
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