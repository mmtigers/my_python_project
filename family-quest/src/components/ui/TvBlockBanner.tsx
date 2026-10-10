import React from 'react';
import { TvBlockState } from '../../types';

interface Props {
    tvBlock: TvBlockState;
    // 禁止開始までの残り秒数(ローカルカウントダウン済み)。禁止中・今日もう禁止が無いときはnull。
    tvSecondsLeft: number | null;
    // いま禁止時間帯として扱うか(サーバーのis_blocked、または残り秒数が0になった直後)。
    tvBlockActive: boolean;
    // 画面上部で全員に見せる用の大きめ表示。ごほうび画面の中では省略(通常サイズ)。
    large?: boolean;
}

// 休日のテレビおやすみの予告・表示。朝から常時出し、禁止開始の30分前/10分前で色を変える。
// ごほうび画面(InventoryList)と画面上部(TvBlockHeaderBanner)の両方がこれを使い、
// 判定・文言を1か所に保つ(片方だけ食い違うのを防ぐ)。
const TvBlockBanner: React.FC<Props> = ({ tvBlock, tvSecondsLeft, tvBlockActive, large = false }) => {
    const textSize = large ? 'text-sm' : 'text-xs';
    const iconSize = large ? 'text-xl' : 'text-base';
    const padding = large ? 'p-3' : 'p-2';

    if (tvBlockActive) {
        return (
            <div
                role="status"
                className={`flex items-center gap-2 ${padding} rounded-xl border ${textSize} bg-slate-100 border-slate-300 text-slate-600`}
            >
                <span className={`${iconSize} leading-none flex-shrink-0`}>🌙</span>
                <p>
                    <span className="font-bold">いまテレビおやすみ中</span>
                    {tvBlock.blocked_until ? `(${tvBlock.blocked_until}まで)` : '(あしたまで)'}
                </p>
            </div>
        );
    }

    const scheduleText = tvBlock.windows
        .map((w) => (w.end ? `${w.start}〜${w.end}` : `${w.start}から`))
        .join(' と ');
    const tvMinutesLeft = tvSecondsLeft === null ? null : Math.ceil(tvSecondsLeft / 60);
    const soon = tvMinutesLeft !== null && tvMinutesLeft <= 30;
    const urgent = tvMinutesLeft !== null && tvMinutesLeft <= 10;
    const tone = urgent
        ? 'bg-orange-50 border-orange-400 text-orange-800'
        : soon
        ? 'bg-amber-50 border-amber-300 text-amber-800'
        : 'bg-indigo-50 border-indigo-200 text-indigo-800';
    return (
        <div role="status" className={`flex items-center gap-2 ${padding} rounded-xl border ${textSize} ${tone}`}>
            <span className={`${iconSize} leading-none flex-shrink-0`}>{soon ? '⏰' : '🌙'}</span>
            <p>
                {soon && tvMinutesLeft !== null ? (
                    <>
                        <span className="font-bold">あと{tvMinutesLeft}分</span>
                        で{tvBlock.next_block_starts_at}からテレビがおやすみ。キリのいいところまでね
                    </>
                ) : (
                    <>
                        きょうは <span className="font-bold">{scheduleText}</span> テレビおやすみ
                    </>
                )}
            </p>
        </div>
    );
};

export default TvBlockBanner;
