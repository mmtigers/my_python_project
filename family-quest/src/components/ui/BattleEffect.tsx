import React, { useEffect, useState } from 'react';
import { Boss, BossEffect } from '@/types'; // ★ BossEffect をインポート


interface BattleEffectProps {
    effect: BossEffect | null;
    boss: Boss | null;
    onClose: () => void;
}

const BattleEffect: React.FC<BattleEffectProps> = ({ effect, boss, onClose }) => {
    const [visible, setVisible] = useState(false);
    const [phase, setPhase] = useState<'enter' | 'hit' | 'leave'>('enter');

    useEffect(() => {
        if (effect) {
            setVisible(true);
            setPhase('enter');

            // アニメーションシーケンス
            const timer1 = setTimeout(() => setPhase('hit'), 100); // 登場直後にヒット
            const timer2 = setTimeout(() => setPhase('leave'), 2500); // 2.5秒後に退出開始
            const timer3 = setTimeout(() => {
                setVisible(false);
                onClose();
            }, 3000); // 完全に消える

            return () => {
                clearTimeout(timer1);
                clearTimeout(timer2);
                clearTimeout(timer3);
            };
        }
    }, [effect, onClose]);

    if (!visible || !effect || !boss) return null;

    const isCritical = effect.isCritical;
    const isDefeated = effect.isNewDefeat || effect.isDefeated;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm animate-in fade-in duration-200">
            <div className={`relative flex flex-col items-center justify-center transform transition-all duration-300 ${phase === 'hit' ? 'scale-110' : 'scale-100'}`}>

                {/* Boss Icon with Shake Effect */}
                <div className={`text-9xl mb-4 filter drop-shadow-[0_0_20px_rgba(255,0,0,0.5)] ${phase === 'hit' ? 'animate-[wiggle_0.3s_ease-in-out_infinite]' : ''}`}>
                    {boss.bossIcon}
                </div>

                {/* Damage Number */}
                <div className="relative">
                    <div className={`text-6xl font-black italic tracking-tighter text-transparent bg-clip-text bg-gradient-to-b from-yellow-300 to-red-600 drop-shadow-lg transform transition-all duration-100 
                        ${phase === 'hit' ? 'translate-y-0 opacity-100 scale-125' : 'translate-y-10 opacity-0 scale-50'}`}>
                        {effect.damage}
                    </div>
                    {isCritical && (
                        <div className="absolute -top-8 -right-12 text-2xl font-bold text-yellow-100 bg-red-600 px-2 py-1 rotate-12 border-2 border-yellow-400 shadow-lg animate-pulse">
                            CRITICAL!!
                        </div>
                    )}
                </div>

                {/* Message */}
                <div className="mt-4 text-white font-bold text-lg bg-black/50 px-4 py-1 rounded-full border border-white/20">
                    {boss.bossName} にダメージ！
                </div>

                {/* Defeated Badge */}
                {isDefeated && (
                    <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full text-center">
                        <h2 className="text-5xl font-black text-yellow-400 drop-shadow-[0_0_10px_black] animate-bounce border-y-8 border-yellow-600 bg-black/50 py-4 transform -rotate-12">
                            撃破!!
                        </h2>
                    </div>
                )}
            </div>
        </div>
    );
};

export default BattleEffect;