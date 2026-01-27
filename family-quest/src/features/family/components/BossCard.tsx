import React from 'react';
import { AlertTriangle, CheckCircle2 } from 'lucide-react';
import { Boss } from '@/types';

interface BossCardProps {
    boss: Boss | null;
}

const BossCard: React.FC<BossCardProps> = ({ boss }) => {
    if (!boss) return null;

    // HPバーの色決定
    const getHpColor = (pct: number) => {
        if (pct > 50) return 'bg-green-500';
        if (pct > 20) return 'bg-yellow-400';
        return 'bg-red-500 animate-pulse';
    };

    return (
        <div className="relative border-4 border-double border-red-900 bg-gray-900/90 p-4 shadow-xl rounded-lg overflow-hidden group">
            {/* 背景装飾（アニメーション） */}
            <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_var(--tw-gradient-stops))] from-red-900/20 via-black to-black opacity-50 animate-pulse"></div>

            {/* 撃破済みオーバーレイ */}
            {boss.isDefeated && (
                <div className="absolute inset-0 z-20 flex flex-col items-center justify-center bg-black/70 backdrop-blur-sm animate-in zoom-in duration-500">
                    <CheckCircle2 size={64} className="text-yellow-400 mb-2 drop-shadow-[0_0_15px_rgba(250,204,21,0.8)]" />
                    <h2 className="text-3xl font-black text-white tracking-widest border-y-4 border-yellow-500 py-1 px-4 bg-yellow-500/20 transform -rotate-6">
                        VICTORY!!
                    </h2>
                    <p className="text-yellow-200 mt-2 font-bold text-sm">今週のボスを撃破しました！</p>
                </div>
            )}

            <div className="relative z-10 flex flex-col items-center text-center">
                {/* ヘッダー */}
                <div className="flex items-center gap-2 mb-3">
                    <AlertTriangle size={18} className="text-red-500 animate-bounce" />
                    <span className="text-red-500 font-bold tracking-[0.2em] text-xs">WARNING: WEEKLY BOSS</span>
                    <AlertTriangle size={18} className="text-red-500 animate-bounce" />
                </div>

                {/* ボスアイコン & 名前 */}
                <div className="mb-2 transform group-hover:scale-110 transition-transform duration-300">
                    <div className="text-6xl filter drop-shadow-[0_0_10px_rgba(220,38,38,0.5)]">
                        {boss.bossIcon}
                    </div>
                </div>
                <h3 className="text-xl font-black text-white mb-1 drop-shadow-md">
                    {boss.bossName}
                </h3>
                <p className="text-[10px] text-gray-400 mb-4 max-w-[80%]">
                    {boss.desc}
                </p>

                {/* HPバー */}
                <div className="w-full max-w-xs space-y-1">
                    <div className="flex justify-between text-[10px] font-bold px-1">
                        <span className="text-red-300">HP</span>
                        <span className="text-white">{boss.currentHp} / {boss.maxHp}</span>
                    </div>
                    <div className="h-4 w-full bg-gray-800 rounded-full border border-gray-600 overflow-hidden relative shadow-inner">
                        <div
                            className={`h-full ${getHpColor(boss.hpPercentage)} transition-all duration-1000 ease-out flex items-center justify-end pr-1`}
                            style={{ width: `${boss.hpPercentage}%` }}
                        >
                            <span className="text-[8px] text-black/50 font-bold mix-blend-overlay">
                                {Math.round(boss.hpPercentage)}%
                            </span>
                        </div>
                        {/* ダメージ予測の白いバーなどを入れたければここに追加 */}
                    </div>
                </div>

                {/* 週間情報 */}
                <div className="mt-4 text-[9px] text-gray-500 font-mono">
                    ターゲット期間: {boss.weekStartDate} 〜
                </div>
            </div>
        </div>
    );
};

export default BossCard;