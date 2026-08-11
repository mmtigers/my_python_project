import { useQuery } from '@tanstack/react-query';
import { Trophy, Coins, Star, Crown, Medal } from 'lucide-react';
import { apiClient } from '@/lib/apiClient';

// 型定義
interface TrendData {
    startDate: string;
    endDate: string;
    dailyStats: Array<{
        date: string;
        day_label: string;
        users: { [key: string]: { exp: number; gold: number } }
    }>;
    rankings: {
        exp: Array<any>;
        gold: Array<any>;
        count: Array<any>;
    };
    mvp: any;
    mostPopularQuest: string;
}

// アバター表示用コンポーネント
const AvatarDisplay = ({ avatar, sizeClass, borderClass }: { avatar: string, sizeClass: string, borderClass?: string }) => {
    const isImagePath = avatar && typeof avatar === 'string' && avatar.startsWith('/');

    if (isImagePath) {
        let imgSizeClass = 'w-10 h-10'; // default
        if (sizeClass.includes('text-6xl') || sizeClass.includes('text-5xl')) {
            imgSizeClass = 'w-20 h-20 md:w-24 md:h-24';
        } else if (sizeClass.includes('text-4xl')) {
            imgSizeClass = 'w-16 h-16';
        } else if (sizeClass.includes('text-3xl')) {
            imgSizeClass = 'w-12 h-12';
        } else if (sizeClass.includes('text-xl')) {
            imgSizeClass = 'w-10 h-10';
        }

        return (
            <img
                src={avatar}
                alt="avatar"
                className={`${imgSizeClass} rounded-full object-cover shadow-sm bg-gray-700 ${borderClass || 'border-2 border-white/20'}`}
            />
        );
    }

    return <span className={`${sizeClass} drop-shadow-md`}>{avatar}</span>;
};

// 順位バッジ
const RankBadge = ({ rank }: { rank: number }) => {
    if (rank === 1) return <Crown size={24} className="text-yellow-400 fill-yellow-400 drop-shadow-sm" />;
    if (rank === 2) return <Medal size={20} className="text-gray-300 fill-gray-300 drop-shadow-sm" />;
    if (rank === 3) return <Medal size={20} className="text-amber-700 fill-amber-700 drop-shadow-sm" />;
    return <span className="text-gray-400 font-bold w-6 text-center text-sm">{rank}</span>;
};

export const WeeklyTrends = () => {
    // useGameData.ts の他クエリと同様のポーリング方針 (staleTime/refetchInterval) を踏襲
    const { data, isLoading: loading } = useQuery<TrendData>({
        queryKey: ['weeklyTrends'],
        queryFn: () => apiClient.get<TrendData>('/api/quest/analytics/weekly'),
        staleTime: 1000 * 60,
        refetchInterval: 1000 * 30,
    });

    if (loading) return <div className="p-12 text-center text-gray-400 animate-pulse">データを集計中...</div>;
    if (!data) return <div className="p-12 text-center text-gray-500">データがありません</div>;

    // ランキングカード生成用ヘルパー
    const renderRankingCard = (
        title: string,
        icon: React.ReactNode,
        rankData: any[],
        unit: string,
        colorTheme: 'amber' | 'blue'
    ) => {
        const topUser = rankData.length > 0 ? rankData[0] : null;
        const otherUsers = rankData.length > 1 ? rankData.slice(1) : [];

        // テーマカラー設定
        const bgGradient = colorTheme === 'amber'
            ? 'from-amber-900/60 to-gray-900 border-amber-500/30'
            : 'from-blue-900/60 to-gray-900 border-blue-500/30';

        const accentColor = colorTheme === 'amber' ? 'text-amber-400' : 'text-blue-400';
        const subColor = colorTheme === 'amber' ? 'text-amber-100' : 'text-blue-100';
        const glowColor = colorTheme === 'amber' ? 'bg-amber-500/20 group-hover:bg-amber-500/30' : 'bg-blue-500/20 group-hover:bg-blue-500/30';

        return (
            <div className={`relative overflow-hidden rounded-2xl bg-gradient-to-b ${bgGradient} border shadow-xl flex flex-col h-full`}>
                {/* ヘッダー */}
                <div className="p-4 flex items-center justify-center gap-2 border-b border-white/5 bg-black/20">
                    {icon}
                    <h3 className={`font-bold text-lg ${subColor} tracking-wide`}>{title}</h3>
                </div>

                {/* No.1 ユーザー表示エリア */}
                <div className="p-6 flex flex-col items-center text-center relative z-10 flex-grow shrink-0 justify-center">
                    {topUser ? (
                        <>
                            <div className="mb-4 relative group">
                                <div className={`absolute -inset-6 rounded-full blur-xl transition-all ${glowColor}`}></div>
                                <div className="relative">
                                    <AvatarDisplay
                                        avatar={topUser.avatar}
                                        sizeClass="text-5xl"
                                        borderClass={`border-4 ${colorTheme === 'amber' ? 'border-amber-400' : 'border-blue-400'} shadow-xl`}
                                    />
                                    <div className={`absolute -top-3 -right-3 ${colorTheme === 'amber' ? 'bg-amber-500' : 'bg-blue-500'} text-white font-black text-xs px-2 py-1 rounded-full border-2 border-white/20 shadow-lg transform rotate-12`}>
                                        1st
                                    </div>
                                </div>
                            </div>

                            <div className="text-2xl font-black text-white mb-2 drop-shadow-md">{topUser.user_name}</div>
                            <div className={`text-3xl font-mono font-bold ${accentColor} drop-shadow-sm`}>
                                {topUser.value.toLocaleString()} <span className="text-base font-normal opacity-80">{unit}</span>
                            </div>
                        </>
                    ) : (
                        <div className="text-gray-500 py-8">データなし</div>
                    )}
                </div>

                {/* 2位以下のリスト (存在する場合のみ) */}
                {otherUsers.length > 0 && (
                    <div className="bg-black/40 border-t border-white/5 p-3">
                        <ul className="space-y-2">
                            {otherUsers.map((r, i) => (
                                <li key={r.user_id} className="flex items-center justify-between p-2 rounded-lg bg-white/5 hover:bg-white/10 transition-colors">
                                    <div className="flex items-center gap-3">
                                        <div className="w-6 flex justify-center"><RankBadge rank={i + 2} /></div>
                                        <AvatarDisplay avatar={r.avatar} sizeClass="text-lg" borderClass="border border-white/20" />
                                        <span className="text-sm font-medium text-gray-300">{r.user_name}</span>
                                    </div>
                                    <span className={`font-mono ${accentColor} font-bold text-sm`}>
                                        {r.value.toLocaleString()} {unit}
                                    </span>
                                </li>
                            ))}
                        </ul>
                    </div>
                )}
            </div>
        );
    };

    return (
        <div className="space-y-8 animate-fade-in pb-24 max-w-4xl mx-auto">

            {/* 期間表示 */}
            <div className="text-center">
                <span className="inline-block px-4 py-1 rounded-full bg-gray-800 text-gray-400 text-xs font-mono border border-gray-700">
                    集計期間: {data.startDate.slice(5).replace('-', '/')} 〜 {data.endDate.slice(5).replace('-', '/')}
                </span>
            </div>

            {/* 今週のMVP (総合経験値) */}
            {data.mvp && (
                <div className="relative overflow-hidden rounded-2xl bg-gradient-to-b from-yellow-700/40 to-gray-900 border border-yellow-500/30 shadow-2xl transform hover:scale-[1.01] transition-transform duration-300">
                    <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-transparent via-yellow-400 to-transparent opacity-50"></div>

                    {/* 背景装飾 */}
                    <div className="absolute -right-10 -top-10 text-yellow-500/5 rotate-12">
                        <Trophy size={300} />
                    </div>

                    <div className="p-8 flex flex-col items-center text-center relative z-10">
                        <div className="flex items-center gap-3 mb-6 bg-black/30 px-6 py-2 rounded-full border border-yellow-500/20 backdrop-blur-sm">
                            <Crown className="text-yellow-400 drop-shadow-[0_0_8px_rgba(250,204,21,0.6)]" size={24} />
                            <h2 className="text-xl font-bold text-yellow-100 tracking-wider">WEEKLY MVP</h2>
                            <Crown className="text-yellow-400 drop-shadow-[0_0_8px_rgba(250,204,21,0.6)]" size={24} />
                        </div>

                        <div className="mb-4 relative group">
                            <div className="absolute -inset-8 bg-yellow-500/20 rounded-full blur-2xl group-hover:bg-yellow-500/30 transition-all duration-500"></div>
                            <AvatarDisplay avatar={data.mvp.avatar} sizeClass="text-6xl" borderClass="border-[6px] border-yellow-400 shadow-[0_0_20px_rgba(234,179,8,0.4)]" />
                            <div className="absolute -bottom-4 left-1/2 transform -translate-x-1/2 bg-gradient-to-r from-yellow-600 to-yellow-500 text-white font-bold text-sm px-4 py-0.5 rounded-full border border-yellow-300 shadow-lg whitespace-nowrap">
                                総合No.1
                            </div>
                        </div>

                        <div className="text-4xl font-black text-white mb-2 drop-shadow-lg mt-4">{data.mvp.user_name}</div>
                        <div className="text-yellow-200/80 font-mono mb-1 text-sm">Total Experience</div>
                        <div className="text-5xl font-mono font-bold text-transparent bg-clip-text bg-gradient-to-b from-yellow-300 to-yellow-600 drop-shadow-sm">
                            {data.mvp.value.toLocaleString()} <span className="text-2xl text-yellow-500">XP</span>
                        </div>
                    </div>
                </div>
            )}

            {/* ランキンググリッド */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {/* お金持ちランキング */}
                {renderRankingCard(
                    "お金持ちランキング",
                    <Coins className="text-amber-400" size={24} />,
                    data.rankings.gold,
                    "G",
                    "amber"
                )}

                {/* 頑張りランキング */}
                {renderRankingCard(
                    "頑張りランキング",
                    <Star className="text-blue-400" size={24} />,
                    data.rankings.count,
                    "回",
                    "blue"
                )}
            </div>

            {/* フッター情報 */}
            <div className="text-center pt-4">
                <div className="inline-block px-6 py-3 bg-gray-800/80 rounded-full border border-gray-700 shadow-lg">
                    <p className="text-xs text-gray-400 mb-1">🔥 今週一番人気のクエスト</p>
                    <p className="text-base text-white font-bold tracking-wide">{data.mostPopularQuest}</p>
                </div>
            </div>

        </div>
    );
};