import React, { useEffect, useState } from 'react';

interface Props {
    durationMs: number;
    size?: number;
}

// 無限クエストの連打防止クールダウン(60秒)を、テキストだけでなく
// 残り時間が視覚的にわかる円形プログレスリングとして表示する。
export const CooldownRing: React.FC<Props> = ({ durationMs, size = 40 }) => {
    const [remainingFraction, setRemainingFraction] = useState(1);

    useEffect(() => {
        // #477: durationMs<=0だと elapsed/durationMs がInfinity/NaNになるため、
        // タイマー自体を張らない。完了状態(0)は下の描画時の算出で表す
        // (以前はここで setRemainingFraction(0) していたが、effect 内で同期的に
        //  setState すると再描画が連鎖するため、react-hooks 7 のルールで禁じられた)。
        if (durationMs <= 0) {
            return;
        }
        const startedAt = Date.now();
        const id = window.setInterval(() => {
            const elapsed = Date.now() - startedAt;
            const frac = Math.max(0, 1 - elapsed / durationMs);
            setRemainingFraction(frac);
            if (frac <= 0) window.clearInterval(id);
        }, 100);
        return () => window.clearInterval(id);
    }, [durationMs]);

    const strokeWidth = 3;
    const radius = size / 2 - strokeWidth;
    const circumference = 2 * Math.PI * radius;
    const fraction = durationMs <= 0 ? 0 : remainingFraction;
    const dashoffset = circumference * (1 - fraction);

    return (
        <svg width={size} height={size} className="-rotate-90" role="img" aria-label="クールダウン中">
            <circle
                cx={size / 2}
                cy={size / 2}
                r={radius}
                fill="none"
                stroke="rgba(255,255,255,0.25)"
                strokeWidth={strokeWidth}
            />
            <circle
                cx={size / 2}
                cy={size / 2}
                r={radius}
                fill="none"
                stroke="#67e8f9"
                strokeWidth={strokeWidth}
                strokeLinecap="round"
                strokeDasharray={circumference}
                strokeDashoffset={dashoffset}
                style={{ transition: 'stroke-dashoffset 100ms linear' }}
            />
        </svg>
    );
};

export default CooldownRing;
