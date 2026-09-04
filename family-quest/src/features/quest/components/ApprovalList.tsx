import React, { useState } from 'react';
import { CheckCircle, XCircle, ChevronDown, ChevronUp, CheckCheck } from 'lucide-react';
import { motion, useMotionValue, useTransform, PanInfo } from 'framer-motion';
import { ID, QuestHistory, User } from '@/types';
import { Button } from '../../../components/ui/Button';

type Props = {
    pendingQuests: QuestHistory[];
    users: User[];
    onApprove: (history: QuestHistory) => void;
    onReject: (history: QuestHistory) => void;
    onApproveAll: () => void;
    // #391(F-L8): 承認APIが送信中の履歴id(App側の approvingHistoryIdsRef の写し)。
    // 該当行の承認/却下ボタンをローディング表示にし、スワイプも無効化する。
    busyHistoryIds?: ID[];
    // 一括承認の実行中。「すべて承認」ボタンをローディング表示にする。
    isApprovingAll?: boolean;
};

const SWIPE_THRESHOLD = 90;

// スワイプで承認/却下できる行ラッパー。右スワイプ=承認、左スワイプ=却下。
// ボタンは廃止せず併存させ、スワイプに気づかない人でも従来通り操作できるようにする。
const SwipeableRow: React.FC<{
    onSwipeApprove?: () => void;
    onSwipeReject?: () => void;
    children: React.ReactNode;
}> = ({ onSwipeApprove, onSwipeReject, children }) => {
    const x = useMotionValue(0);
    const background = useTransform(
        x,
        [-SWIPE_THRESHOLD - 30, 0, SWIPE_THRESHOLD + 30],
        ['rgba(220, 38, 38, 0.35)', 'rgba(0,0,0,0)', 'rgba(22, 163, 74, 0.35)']
    );

    const handleDragEnd = (_e: MouseEvent | TouchEvent | PointerEvent, info: PanInfo) => {
        if (info.offset.x > SWIPE_THRESHOLD && onSwipeApprove) onSwipeApprove();
        else if (info.offset.x < -SWIPE_THRESHOLD && onSwipeReject) onSwipeReject();
    };

    const draggable = !!(onSwipeApprove || onSwipeReject);

    return (
        <motion.div
            style={{ x, background }}
            drag={draggable ? 'x' : false}
            dragConstraints={{ left: 0, right: 0 }}
            dragElastic={0.6}
            onDragEnd={handleDragEnd}
            className="rounded"
        >
            {children}
        </motion.div>
    );
};

const ApprovalList: React.FC<Props> = ({ pendingQuests, users, onApprove, onReject, onApproveAll, busyHistoryIds = [], isApprovingAll = false }) => {
    // 承認待ちが多いときに折りたためるように(デフォルトは開いた状態)
    const [collapsed, setCollapsed] = useState(false);

    const getUserName = (userId: string) => {
        return users.find(u => u.user_id === userId)?.name || userId;
    };

    if (pendingQuests.length === 0) return null;

    const totalCount = pendingQuests.length;

    return (
        <div className="bg-yellow-50 border-l-4 border-yellow-400 rounded shadow-sm animate-fade-in overflow-hidden">
            <button
                onClick={() => setCollapsed(c => !c)}
                className="w-full min-h-[44px] flex items-center justify-between gap-2 px-4 py-2 font-bold text-yellow-800"
            >
                <span className="flex items-center gap-2">
                    <span className="animate-pulse">🔔</span> 承認待ちのアクション
                    <span className="bg-yellow-500 text-white text-xs px-2 py-0.5 rounded-full">{totalCount}</span>
                </span>
                {collapsed ? <ChevronDown size={18} /> : <ChevronUp size={18} />}
            </button>

            {!collapsed && (
                <div className="px-4 pb-4">
                    {pendingQuests.length > 1 && (
                        <div className="flex justify-end mb-2">
                            <Button variant="success" size="sm" onClick={onApproveAll} isLoading={isApprovingAll}>
                                <CheckCheck size={16} /> クエストをすべて承認 ({pendingQuests.length})
                            </Button>
                        </div>
                    )}

                    <p className="text-[11px] text-yellow-700/70 mb-2">→ スワイプで承認 / ← スワイプで却下</p>

                    <div className="space-y-3">
                        {/* --- クエスト承認リスト (既存) --- */}
                        {pendingQuests.map((quest) => {
                            // #391(F-L8): 送信中(個別承認の応答待ち、または一括承認の対象)の行は
                            // ボタンをローディング表示にし、スワイプも受け付けない。
                            const busy = quest.id != null && busyHistoryIds.includes(quest.id);
                            return (
                            <SwipeableRow
                                key={quest.id}
                                onSwipeApprove={busy ? undefined : () => onApprove(quest)}
                                onSwipeReject={busy ? undefined : () => onReject(quest)}
                            >
                                <div className="bg-white p-3 rounded shadow-sm flex justify-between items-center gap-2">
                                    <div className="min-w-0">
                                        <p className="font-bold text-gray-800">
                                            <span className="text-sm bg-blue-100 text-blue-800 px-2 py-0.5 rounded-full mr-2">
                                                クエスト
                                            </span>
                                            {quest.quest_title}
                                        </p>
                                        <p className="text-sm text-gray-500">
                                            担当: {getUserName(quest.user_id)} / 報酬: {quest.gold_earned}G
                                        </p>
                                    </div>
                                    <div className="flex gap-2 flex-shrink-0">
                                        <Button variant="danger" size="sm" className="min-h-[44px] min-w-[44px]" onClick={() => onReject(quest)} disabled={busy}>
                                            <XCircle size={18} />
                                        </Button>
                                        <Button variant="primary" size="sm" className="min-h-[44px]" onClick={() => onApprove(quest)} isLoading={busy}>
                                            <CheckCircle size={18} /> 承認
                                        </Button>
                                    </div>
                                </div>
                            </SwipeableRow>
                            );
                        })}
                    </div>
                </div>
            )}
        </div>
    );
};

export default ApprovalList;
