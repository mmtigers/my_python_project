import React, { useMemo } from 'react';
import { Sword, Shield, Crown, Skull } from 'lucide-react';

/**
 * ドラクエ風 パーティステータス画面
 */
const FamilyParty = ({ users, ownedEquipments }) => {
    // ユーザーごとの詳細ステータスを計算
    const partyData = useMemo(() => {
        return users.map(user => {
            // 装備品を取得
            const myEquips = ownedEquipments.filter(e => e.user_id === user.user_id && e.is_equipped === 1);
            const weapon = myEquips.find(e => e.type === 'weapon');
            const armor = myEquips.find(e => e.type === 'armor');

            // ステータス計算 (仮の計算式)
            // 攻撃力 = Lv * 3 + 武器の強さ
            // 守備力 = Lv * 2 + 防具の強さ
            const baseAtk = user.level * 3;
            const baseDef = user.level * 2;
            const totalAtk = baseAtk + (weapon?.power || 0);
            const totalDef = baseDef + (armor?.power || 0);

            return {
                ...user,
                weapon,
                armor,
                stats: {
                    atk: totalAtk,
                    def: totalDef
                }
            };
        });
    }, [users, ownedEquipments]);

    return (
        <div className="animate-in fade-in duration-500 space-y-4 font-mono">
            {/* ヘッダー枠 */}
            <div className="border-4 border-double border-white bg-black p-2 text-center shadow-lg">
                <h2 className="text-xl font-bold text-yellow-400 tracking-widest flex items-center justify-center gap-2">
                    <Crown size={20} />
                    パーティ の つよさ
                    <Crown size={20} />
                </h2>
            </div>

            {/* キャラクターリスト */}
            <div className="space-y-3">
                {partyData.map((member) => (
                    <div key={member.user_id} className="border-2 border-white bg-blue-950/80 p-1 relative shadow-md">
                        {/* 枠のデザイン装飾（四隅） */}
                        <div className="absolute top-0 left-0 w-2 h-2 border-t-2 border-l-2 border-white"></div>
                        <div className="absolute top-0 right-0 w-2 h-2 border-t-2 border-r-2 border-white"></div>
                        <div className="absolute bottom-0 left-0 w-2 h-2 border-b-2 border-l-2 border-white"></div>
                        <div className="absolute bottom-0 right-0 w-2 h-2 border-b-2 border-r-2 border-white"></div>

                        <div className="flex gap-3 p-2">
                            {/* 左側：アバターとレベル */}
                            <div className="flex flex-col items-center justify-center min-w-[60px] border-r-2 border-dashed border-gray-500 pr-2">
                                <div className="text-4xl bg-gray-900 rounded p-1 border border-gray-600 mb-1">
                                    {member.avatar}
                                </div>
                                <div className="text-xs text-yellow-300 font-bold">Lv.{member.level}</div>
                            </div>

                            {/* 右側：詳細ステータス */}
                            <div className="flex-1 space-y-1">
                                {/* 名前と職業 */}
                                <div className="flex justify-between items-baseline border-b border-gray-600 pb-1">
                                    <span className="text-lg font-bold text-white tracking-wider">{member.name}</span>
                                    <span className="text-xs text-cyan-300">{member.job_class}</span>
                                </div>

                                {/* HP / EXP */}
                                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs mt-1">
                                    <div className="flex justify-between text-green-300">
                                        <span>H P</span>
                                        <span className="font-bold text-white">{member.hp} / {member.maxHp}</span>
                                    </div>
                                    <div className="flex justify-between text-orange-300">
                                        <span>EXP</span>
                                        <span className="font-bold text-white">{member.exp}</span>
                                    </div>
                                </div>

                                {/* 攻撃 / 守備 */}
                                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                                    <div className="flex justify-between text-red-300">
                                        <span>こうげき</span>
                                        <span className="font-bold text-white text-base">{member.stats.atk}</span>
                                    </div>
                                    <div className="flex justify-between text-blue-300">
                                        <span>しゅび</span>
                                        <span className="font-bold text-white text-base">{member.stats.def}</span>
                                    </div>
                                </div>

                                {/* 装備品エリア */}
                                <div className="mt-2 pt-1 border-t border-gray-700 space-y-1">
                                    <div className="flex items-center gap-2 text-xs">
                                        <Sword size={12} className="text-gray-400" />
                                        <span className={member.weapon ? "text-white" : "text-gray-600"}>
                                            {member.weapon ? `${member.weapon.name} (+${member.weapon.power})` : "すで"}
                                        </span>
                                    </div>
                                    <div className="flex items-center gap-2 text-xs">
                                        <Shield size={12} className="text-gray-400" />
                                        <span className={member.armor ? "text-white" : "text-gray-600"}>
                                            {member.armor ? `${member.armor.name} (+${member.armor.power})` : "ぬののふく"}
                                        </span>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                ))}
            </div>

            {/* まだ見ぬ敵への予告エリア */}
            <div className="border border-dashed border-gray-600 p-4 text-center text-gray-500 text-xs mt-8">
                <Skull className="mx-auto mb-2 opacity-50" />
                <p>とてつもない きょうてき の けはい がする...</p>
                <p className="mt-1 text-[10px]">（ボスバトル機能 開発中）</p>
            </div>
        </div>
    );
};

export default FamilyParty;