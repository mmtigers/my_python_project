import React, { useMemo } from 'react';
import { Reward, User } from '@/types';
import { Card } from '@/components/ui/Card';

interface RewardListProps {
  rewards: Reward[];
  userGold: number;
  onBuy: (reward: Reward) => void;
  currentUser: User;
}

const RewardList: React.FC<RewardListProps> = ({ rewards, userGold, onBuy, currentUser }) => {
  const sortedRewards = useMemo(() => {
    // 1. フィルタリング
    const filtered = rewards.filter(r => {
      const target = r.target || 'all'; // targetがない場合は全員

      if (target === 'all') return true;

      const isAdult = currentUser.role === 'role_adult';

      if (target === 'children') return !isAdult;
      if (target === 'adults') return isAdult;

      // #239: 上記以外(具体的なuser_id宛て、または将来追加されうる未知のtarget値)は
      // QuestList.tsxのターゲット判定(q.target !== currentUser?.user_id)と同様に
      // 安全側(deny-by-default)へ倒す。以前は'mom'/'dad'以外のtarget('son'/'daughter'等)
      // がどの分岐にも一致せず無条件でtrueになり、対象外の家族全員に表示されていた。
      return target === currentUser.user_id;
    });

    // 2. ソート (安い順)
    return filtered.sort((a, b) => {
      const costA = a.cost_gold || 0;
      const costB = b.cost_gold || 0;
      return costA - costB;
    });
  }, [rewards, currentUser]);

  return (
    <div className="space-y-2 animate-in fade-in slide-in-from-bottom-2 duration-300">
      {sortedRewards.length === 0 && (
        <div className="text-center text-gray-400 py-4 text-xs">商品が入荷待ちです...</div>
      )}

      {sortedRewards.map((reward, index) => {
        const cost = reward.cost_gold || 0;
        const canAfford = userGold >= cost;

        // ★low-priority: reward_id は型上省略可能なため、欠けている
        // (バックエンドのデータ不備) 場合のみ index にフォールバックする。通常データでは
        // 発生しないベストエフォートの保険であり、意図的にそのまま残している。
        const rId = reward.reward_id || index;

        // ★追加: 説明文の優先順位ロジック
        const displayText = reward.description || reward.category || 'General';

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
                {reward.icon_key || '🎁'}
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
            </div>
          </Card>
        );
      })}
    </div>
  );
};

export default RewardList;