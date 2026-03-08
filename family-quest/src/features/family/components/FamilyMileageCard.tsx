import React, { useEffect } from 'react';
import confetti from 'canvas-confetti';
import { FamilyMileage } from '@/types';

interface FamilyMileageCardProps {
    mileage: FamilyMileage | null;
}

export const FamilyMileageCard: React.FC<FamilyMileageCardProps> = ({ mileage }) => {
    if (!mileage || !mileage.is_set) {
        return (
            <div className="bg-gray-800 rounded-lg p-4 mb-4 border-2 border-dashed border-gray-600 text-center shadow-md">
                <p className="text-gray-400 font-bold">共有目標が未設定です</p>
                <p className="text-xs text-gray-500 mt-1">管理画面から新しい目標を設定してください。</p>
            </div>
        );
    }

    const { target_name, current_exp = 0, target_exp = 1 } = mileage;
    // B案: ゲージ幅は100%で止めるが、数値はそのまま表示する
    const progress = Math.min((current_exp / target_exp) * 100, 100);
    const isCompleted = current_exp >= target_exp;

    useEffect(() => {
        if (isCompleted) {
            confetti({
                particleCount: 150,
                spread: 80,
                origin: { y: 0.6 },
                zIndex: 100
            });
        }
    }, [isCompleted, current_exp]);

    return (
        <div className={`rounded-lg p-4 mb-4 border-2 shadow-lg transition-all ${isCompleted ? 'bg-yellow-900 border-yellow-500' : 'bg-gray-800 border-gray-600'}`}>
            <div className="flex justify-between items-center mb-2">
                <h3 className="font-bold text-lg text-white">🏆 家族の目標: {target_name}</h3>
                {isCompleted && <span className="text-yellow-400 font-bold animate-pulse">達成！！</span>}
            </div>

            <div className="w-full bg-gray-700 rounded-full h-5 mb-1 border border-gray-600 relative overflow-hidden shadow-inner">
                <div
                    className={`h-5 rounded-full transition-all duration-1000 ${isCompleted ? 'bg-gradient-to-r from-yellow-400 to-yellow-600' : 'bg-gradient-to-r from-blue-400 to-blue-600'}`}
                    style={{ width: `${progress}%` }}
                ></div>
            </div>

            <div className="flex justify-between text-sm font-mono text-gray-300 px-1">
                <span>{current_exp} EXP</span>
                <span>{target_exp} EXP</span>
            </div>
        </div>
    );
};