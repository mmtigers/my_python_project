import React from 'react';
import { History, Clock } from 'lucide-react';
import { ChronicleItem } from '@/hooks/useGameData';
import { User } from '@/types';
import { isSameOriginAvatarPath } from '../../../lib/utils';

interface FamilyLogProps {
    chronicle: ChronicleItem[];
    users: User[];
}

// 時刻フォーマット関数
const formatTime = (ts: string | number | undefined) => {
    if (!ts) return '';
    const date = new Date(ts);
    return date.toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' });
};

// 冒険の記録(タイムライン)1人分のカラム。ホーム画面(横画面の4人並びパネル)と同様に、
// タブで選ばせるのではなく最初から全員分を並べて表示する。
const UserLogColumn: React.FC<{ user: User; entries: ChronicleItem[] }> = ({ user, entries }) => {

    // 日付ごとにログをグループ化
    const groupedChronicle = entries.reduce((groups: Record<string, ChronicleItem[]>, item: ChronicleItem) => {
        const date = item.dateStr || '----/--/--';
        if (!groups[date]) groups[date] = [];
        groups[date].push(item);
        return groups;
    }, {});

    return (
        <div className="bg-black/20 border border-gray-700 rounded-xl p-3 space-y-3 min-w-0">
            <div className="flex items-center gap-2 border-b border-gray-700 pb-2">
                <span className="w-7 h-7 rounded-full overflow-hidden flex items-center justify-center bg-gray-800 text-sm flex-shrink-0">
                    {isSameOriginAvatarPath(user.avatar) ? (
                        <img src={user.avatar} alt={user.name} className="w-full h-full object-cover" />
                    ) : (
                        user.avatar || '🙂'
                    )}
                </span>
                <h3 className="font-bold text-white text-sm truncate">{user.name}</h3>
            </div>

            {entries.length === 0 && (
                <div className="text-center text-gray-500 text-xs py-4">まだ記録がありません</div>
            )}

            {Object.entries(groupedChronicle).map(([date, logs]: [string, ChronicleItem[]]) => (
                <div key={date} className="relative pl-3 border-l-2 border-gray-700">
                    <div className="absolute -left-[7px] top-0 w-3 h-3 rounded-full bg-blue-500 border-2 border-black"></div>
                    <div className="text-[10px] text-gray-400 mb-1.5 font-bold">{date}</div>

                    <div className="space-y-1.5">
                        {/* #412(F-L2): timestampが同秒のイベントが複数あるとkeyが衝突するため、
                            リスト内の位置も組み合わせて一意にする。 */}
                        {logs.map((log: ChronicleItem, i: number) => (
                            <div key={`${log.timestamp}-${i}`} className="bg-blue-950/30 p-1.5 rounded border border-blue-900/50">
                                <div className="flex items-center gap-1 text-[9px] text-gray-400 mb-0.5">
                                    <Clock size={9} />
                                    {formatTime(log.timestamp)}
                                </div>

                                <div className="text-[11px] text-white leading-snug">
                                    {log.text}
                                </div>
                                <div className="flex gap-1.5 mt-0.5">
                                    {(log.gold || 0) > 0 && (
                                        // M-6-4バグ修正: 報酬購入(type='reward')はゴールドを消費した記録のため
                                        // "-N G"、クエスト達成(type='quest')は獲得のため"+N G"と表示する。
                                        // 以前は購入も一律"+N G"(獲得)表示になっていた。
                                        <span
                                            className={`text-[9px] font-bold px-1 rounded ${log.type === 'reward'
                                                ? 'text-red-400 bg-red-900/30'
                                                : 'text-yellow-400 bg-yellow-900/30'
                                                }`}
                                        >
                                            {log.type === 'reward' ? '-' : '+'}{log.gold} G
                                        </span>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            ))}
        </div>
    );
};

// ★バグ修正: 冒険の記録は以前タブで1人ずつ切り替える形式だったが、ホーム画面(横画面の
// 4人並びパネル)と同様に、最初から全員分を並べて表示する。家族の総力(パーティランク・
// 総レベルなど)の集計表示は不要とのことなので廃止した。
const FamilyLog: React.FC<FamilyLogProps> = ({ chronicle, users }) => {
    if (!chronicle) return <div className="text-center py-10">冒険の記録を読み込んでいます...</div>;

    return (
        <div className="space-y-3 animate-in fade-in duration-500 pb-6">
            <div className="flex items-center gap-2 text-white border-b border-gray-700 pb-2">
                <History className="text-blue-400" />
                <h3 className="font-bold text-lg">冒険の記録</h3>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
                {users.map(user => (
                    <UserLogColumn
                        key={user.user_id}
                        user={user}
                        entries={chronicle.filter(item => item.userId === user.user_id)}
                    />
                ))}
            </div>
        </div>
    );
};

export default FamilyLog;
