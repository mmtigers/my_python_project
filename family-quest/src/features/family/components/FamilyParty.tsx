import React, { useMemo } from 'react';
import { Crown, Sword, Shield } from 'lucide-react';
import { User, Equipment, Boss } from '@/types';
import BossCard from './BossCard'; // ★追加

interface FamilyPartyProps {
    users: User[];
    ownedEquipments: any[]; // 型定義に合わせて調整 (例: Equipment & { is_equipped: number, user_id: string })
    boss: Boss | null; // ★追加
}

const FamilyParty: React.FC<FamilyPartyProps> = ({ users, ownedEquipments, boss }) => {
    // ユーザーごとの詳細ステータスを計算
    const partyData = useMemo(() => {
        return users.map(user => {
            // APIレスポンス構造によっては ownedEquipments の中身を any として扱うか、型定義を強化する
            const myEquips = ownedEquipments.filter((e: any) => e.user_id === user.user_id && e.is_equipped === 1);
            const weapon = myEquips.find((e: any) => e.type === 'weapon');
            const armor = myEquips.find((e: any) => e.type === 'armor');

            // 攻撃力・守備力の計算
            const baseAtk = user.level * 3;
            const baseDef = user.level * 2;
            const totalAtk = baseAtk + (weapon?.power || 0);
            const totalDef = baseDef + (armor?.power || 0);

            // HPの計算 (簡易: Lv * 10 + 50)
            const maxHp = (user.level * 10) + 50;

            return {
                ...user,
                hp: maxHp,
                weapon: weapon as Equipment | undefined,
                armor: armor as Equipment | undefined,
                stats: { atk: totalAtk, def: totalDef }
            };
        });
    }, [users, ownedEquipments]);

    return (
        <div className="animate-in fade-in duration-500 space-y-4 font-mono pb-8">
            {/* ★ここに追加: ボスカード (一番目立つ位置に配置) */}
            <BossCard boss={boss} />

            {/* ヘッダー枠 */}
            <div className="border-4 border-double border-white bg-black p-2 text-center shadow-lg">
                <h2 className="text-xl font-bold text-yellow-400 tracking-widest flex items-center justify-center gap-2">
                    <Crown size={20} />
                    パーティ の つよさ
                    <Crown size={20} />
                </h2>
            </div>

            {/* キャラクターリスト (2列グリッド) */}
            <div className="grid grid-cols-2 gap-2">
                {partyData.map((member) => {
                    return (
                        <div key={member.user_id} className="border-2 border-white bg-blue-950/80 p-1 relative shadow-md flex flex-col">
                            {/* 枠の装飾 */}
                            <div className="absolute top-0 left-0 w-2 h-2 border-t-2 border-l-2 border-white"></div>
                            <div className="absolute top-0 right-0 w-2 h-2 border-t-2 border-r-2 border-white"></div>
                            <div className="absolute bottom-0 left-0 w-2 h-2 border-b-2 border-l-2 border-white"></div>
                            <div className="absolute bottom-0 right-0 w-2 h-2 border-b-2 border-r-2 border-white"></div>

                            {/* 上部：基本情報 */}
                            <div className="flex items-center gap-2 p-2 border-b border-gray-600 bg-black/20">
                                <div
                                    className="w-12 h-12 bg-gray-900 rounded p-1 border border-gray-600 shadow-inner overflow-hidden flex items-center justify-center"
                                >
                                    {member.avatar ? (
                                        <img src={member.avatar} alt={member.name} className="w-full h-full object-cover" />
                                    ) : (
                                        <div className="text-2xl">{member.icon || '🙂'}</div>
                                    )}
                                </div>
                                <div className="flex-1 min-w-0">
                                    <div className="text-sm font-bold text-yellow-300 tracking-wider truncate">
                                        {member.name}
                                    </div>
                                    <div className="text-[10px] text-cyan-300 truncate">
                                        Lv.{member.level} {member.job_class || '冒険者'}
                                    </div>
                                </div>
                            </div>

                            {/* 中部：ステータス */}
                            <div className="p-2 space-y-2">
                                <div className="grid grid-cols-2 gap-1 text-xs">
                                    <div className="bg-gray-900/50 px-1 rounded flex flex-col items-center">
                                        <span className="text-[8px] text-green-400">HP</span>
                                        <span className="text-white font-bold">{member.hp}</span>
                                    </div>
                                    <div className="bg-gray-900/50 px-1 rounded flex flex-col items-center">
                                        <span className="text-[8px] text-orange-400">EXP</span>
                                        <span className="text-white font-bold">{member.exp}</span>
                                    </div>
                                </div>

                                <div className="grid grid-cols-2 gap-2 text-xs text-center mt-1">
                                    <div className="flex flex-col">
                                        <span className="text-[9px] text-red-300">こうげき</span>
                                        <span className="font-bold text-white text-base">{member.stats.atk}</span>
                                    </div>
                                    <div className="flex flex-col">
                                        <span className="text-[9px] text-blue-300">しゅび</span>
                                        <span className="font-bold text-white text-base">{member.stats.def}</span>
                                    </div>
                                </div>
                            </div>

                            {/* 下部：装備詳細 */}
                            <div className="mt-auto bg-black/30 p-2 border-t border-gray-700 space-y-1.5">
                                {/* 武器 */}
                                <div className="flex items-center gap-1.5 overflow-hidden">
                                    <Sword size={12} className={member.weapon ? "text-yellow-400" : "text-gray-600"} />
                                    <div className="flex-1 flex justify-between items-baseline min-w-0">
                                        <span className={`text-[10px] truncate ${member.weapon ? "text-white" : "text-gray-500"}`}>
                                            {member.weapon ? member.weapon.name : "すで"}
                                        </span>
                                        {member.weapon && (
                                            <span className="text-[10px] text-yellow-200 font-bold ml-1">+{member.weapon.power}</span>
                                        )}
                                    </div>
                                </div>
                                {/* 防具 */}
                                <div className="flex items-center gap-1.5 overflow-hidden">
                                    <Shield size={12} className={member.armor ? "text-blue-400" : "text-gray-600"} />
                                    <div className="flex-1 flex justify-between items-baseline min-w-0">
                                        <span className={`text-[10px] truncate ${member.armor ? "text-white" : "text-gray-500"}`}>
                                            {member.armor ? member.armor.name : "ぬののふく"}
                                        </span>
                                        {member.armor && (
                                            <span className="text-[10px] text-blue-200 font-bold ml-1">+{member.armor.power}</span>
                                        )}
                                    </div>
                                </div>
                            </div>

                        </div>
                    );
                })}
            </div>


        </div>
    );
};

export default FamilyParty;