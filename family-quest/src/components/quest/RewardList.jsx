import React from 'react';
import { ShoppingBag, Coins } from 'lucide-react';

/**
 * 報酬(アイテム)リストを表示するコンポーネント
 * @param {Array} rewards - 報酬マスターデータ
 * @param {Object} currentUser - 現在ログイン中のユーザー
 * @param {Function} onBuy - 購入時のコールバック関数
 */
const RewardList = ({ rewards, currentUser, onBuy }) => {
  // ユーザーが購入可能なアイテムのみ表示するフィルターなどを入れる場合はここ
  // 今回は全表示します

  return (
    <div className="space-y-2 animate-in fade-in slide-in-from-bottom-2 duration-300">
      <div className="text-center border-b border-gray-600 pb-1 mb-2 text-yellow-300 text-sm font-bold flex items-center justify-center gap-2">
        <ShoppingBag size={16} />
        <span>-- 商品一覧 --</span>
      </div>
      
      {rewards.length === 0 && (
        <div className="text-center text-gray-500 py-4 text-xs">商品が入荷待ちです...</div>
      )}

      {rewards.map((reward) => {
        const cost = reward.cost_gold || reward.cost || 0;
        const canAfford = (currentUser?.gold || 0) >= cost;
        const rId = reward.reward_id || reward.id;

        return (
          <div
            key={rId}
            onClick={() => canAfford && onBuy(reward)}
            className={`
              border p-2 rounded flex justify-between items-center transition-all select-none
              ${canAfford 
                ? 'border-white bg-blue-900/80 hover:bg-blue-800 hover:border-yellow-200 cursor-pointer active:scale-[0.98]' 
                : 'border-gray-700 bg-gray-900/50 opacity-60 cursor-not-allowed grayscale'}
            `}
          >
            <div className="flex items-center gap-3">
              <span className="text-2xl">{reward.icon || reward.icon_key || '🎁'}</span>
              <div>
                <div className={`font-bold ${canAfford ? 'text-white' : 'text-gray-400'}`}>
                  {reward.title}
                </div>
                <div className="text-xs text-gray-400">{reward.category}</div>
              </div>
            </div>
            
            <div className={`flex items-center gap-1 font-bold ${canAfford ? 'text-yellow-300' : 'text-red-400'}`}>
              {cost.toLocaleString()} <span className="text-[10px]">G</span>
              {!canAfford && <span className="text-[10px] ml-1">(不足)</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default RewardList;