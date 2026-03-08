import React, { useState, useEffect } from 'react';
import { Settings, Save, } from 'lucide-react';
import { Boss } from '@/types';
import { Button } from '@/components/ui/Button';

interface AdminDashboardProps {
    boss: Boss | null;
    onUpdate: (data: { maxHp?: number; currentHp?: number; isDefeated?: boolean }) => Promise<any>;
    onUpdateMileage: (targetName: string, targetExp: number) => Promise<any>;
    onClose: () => void;
}

const AdminDashboard: React.FC<AdminDashboardProps> = ({ boss, onUpdate, onUpdateMileage, onClose }) => {
    const [maxHp, setMaxHp] = useState(1000);
    const [currentHp, setCurrentHp] = useState(1000);
    const [isDefeated, setIsDefeated] = useState(false);

    // 初期値セット
    useEffect(() => {
        if (boss) {
            setMaxHp(boss.maxHp);
            setCurrentHp(boss.currentHp);
            setIsDefeated(boss.isDefeated);
        }
    }, [boss]);

    const handleSave = async () => {
        // 結果を受け取る
        const result = await onUpdate({
            maxHp,
            currentHp,
            isDefeated
        });

        // 成功判定 (useGameData側で { success: true/false } を返しています)
        if (result && result.success) {
            alert("設定を保存しました！");
            onClose(); // 成功したら閉じる
        } else {
            alert("保存に失敗しました。\n・バックエンドサーバーは再起動しましたか？\n・DBマイグレーションは完了していますか？");
        }
    };

    if (!boss) return <div className="text-white">ボスデータがありません</div>;

    return (
        <div className="fixed inset-0 z-50 bg-black text-white font-mono p-4 overflow-y-auto">
            <div className="max-w-md mx-auto space-y-8 mt-10">
                <div className="flex items-center justify-between border-b border-gray-700 pb-4">
                    <h2 className="text-2xl font-bold flex items-center gap-2 text-red-500">
                        <Settings /> ADMIN MODE
                    </h2>
                    <Button variant="secondary" onClick={onClose}>閉じる</Button>
                </div>

                {/* HP操作エリア */}
                <div className="space-y-6 bg-gray-900 p-4 rounded-lg border border-gray-700">
                    <h3 className="text-lg font-bold text-gray-400">ボスステータス調整</h3>

                    <div className="space-y-2">
                        <label className="block text-sm">最大HP: {maxHp}</label>
                        <input
                            type="range" min="100" max="10000" step="100"
                            value={maxHp}
                            onChange={(e) => setMaxHp(Number(e.target.value))}
                            className="w-full accent-red-500"
                        />
                    </div>

                    <div className="space-y-2">
                        <label className="block text-sm">現在HP: {currentHp}</label>
                        <input
                            type="range" min="0" max={maxHp} step="50"
                            value={currentHp}
                            onChange={(e) => setCurrentHp(Number(e.target.value))}
                            className="w-full accent-green-500"
                        />
                    </div>

                    <div className="flex items-center gap-4">
                        <label className="block text-sm">撃破フラグ:</label>
                        <button
                            onClick={() => setIsDefeated(!isDefeated)}
                            className={`px-4 py-1 rounded font-bold ${isDefeated ? 'bg-green-600' : 'bg-gray-600'}`}
                        >
                            {isDefeated ? "撃破済み (TRUE)" : "未撃破 (FALSE)"}
                        </button>
                    </div>

                    <Button onClick={handleSave} className="w-full flex items-center justify-center gap-2">
                        <Save size={18} /> 反映する
                    </Button>
                </div>

                {/* プリセット操作 */}
                <div className="grid grid-cols-2 gap-4">
                    <Button variant="danger" onClick={() => {
                        setCurrentHp(0);
                        setIsDefeated(true);
                    }}>
                        一撃で倒す (Debug)
                    </Button>
                    <Button variant="secondary" onClick={() => {
                        setCurrentHp(maxHp);
                        setIsDefeated(false);
                    }}>
                        全回復 (Reset)
                    </Button>
                </div>

                {/* 共有目標（マイレージ）操作エリア */}
                <div className="space-y-6 bg-gray-900 p-4 rounded-lg border border-gray-700 mt-8">
                    <h3 className="text-lg font-bold text-gray-400">共有目標（ファミリーマイレージ）</h3>
                    <div className="space-y-2">
                        <label className="block text-sm">新しい目標名</label>
                        <input
                            type="text"
                            id="mileageNameInput"
                            className="w-full bg-gray-800 text-white p-2 rounded border border-gray-600"
                            placeholder="例: 週末は焼肉！"
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="block text-sm">目標EXP</label>
                        <input
                            type="number"
                            id="mileageExpInput"
                            defaultValue={1000}
                            className="w-full bg-gray-800 text-white p-2 rounded border border-gray-600"
                        />
                    </div>
                    <Button
                        onClick={async () => { // ★非同期処理に変更
                            const name = (document.getElementById('mileageNameInput') as HTMLInputElement).value;
                            const exp = Number((document.getElementById('mileageExpInput') as HTMLInputElement).value);

                            if (name && exp) {
                                // ★ 実際のAPI処理を実行
                                const res = await onUpdateMileage(name, exp);
                                if (res && res.success) {
                                    alert("新しい共有目標を設定しました！");
                                    onClose(); // 成功したら管理画面を閉じる
                                } else {
                                    alert("目標の設定に失敗しました");
                                }
                            } else {
                                alert("目標名と目標EXPの両方を入力してください");
                            }
                        }}
                        className="w-full flex items-center justify-center gap-2 bg-blue-600"
                    >
                        <Save size={18} /> 目標を設定してリセット
                    </Button>
                </div>
            </div>
        </div>
    );
};

export default AdminDashboard;