import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { CheckCircle, XCircle, Package, ChevronDown, ChevronUp, CheckCheck } from 'lucide-react';
import { motion, useMotionValue, useTransform, PanInfo } from 'framer-motion';
import { QuestHistory, User, PendingInventory } from '@/types';
import { Button } from '../../../components/ui/Button';
import { Modal } from '../../../components/ui/Modal';
import { apiClient } from '../../../lib/apiClient';

type Props = {
    pendingQuests: QuestHistory[];
    pendingItems: PendingInventory[];
    users: User[];
    currentUser: User;
    onApprove: (history: QuestHistory) => void;
    onReject: (history: QuestHistory) => void;
    onApproveAll: () => void;
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

const ApprovalList: React.FC<Props> = ({ pendingQuests, pendingItems, users, currentUser, onApprove, onReject, onApproveAll }) => {
    const queryClient = useQueryClient();
    // ★変更: 素の confirm() を廃止し、アプリ標準の Modal で「アイテム使用承認」の確認を行う
    const [itemToConsume, setItemToConsume] = useState<PendingInventory | null>(null);
    // 承認待ちが多いときに折りたためるように(デフォルトは開いた状態)
    const [collapsed, setCollapsed] = useState(false);

    // アイテム承認（消費）アクション
    const consumeMutation = useMutation({
        mutationFn: (inventoryId: number) => apiClient.consumeItem(currentUser.user_id, inventoryId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['pendingInventory'] });
            // 必要に応じて親のインベントリリストなども更新
            queryClient.invalidateQueries({ queryKey: ['inventory'] });
            // H-5: アイテム使用の確定(quest_historyへの記録)はconsume_item時に
            // 行われるため、冒険の記録(chronicle)もここで無効化する。
            queryClient.invalidateQueries({ queryKey: ['chronicle'] });
        }
    });

    const getUserName = (userId: string) => {
        return users.find(u => u.user_id === userId)?.name || userId;
    };

    const hasQuests = pendingQuests.length > 0;
    const hasItems = pendingItems && pendingItems.length > 0;

    if (!hasQuests && !hasItems) return null;

    const totalCount = pendingQuests.length + (pendingItems?.length || 0);

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
                            <Button variant="success" size="sm" onClick={onApproveAll}>
                                <CheckCheck size={16} /> クエストをすべて承認 ({pendingQuests.length})
                            </Button>
                        </div>
                    )}

                    <p className="text-[11px] text-yellow-700/70 mb-2">→ スワイプで承認 / ← スワイプで却下</p>

                    <div className="space-y-3">
                        {/* --- クエスト承認リスト (既存) --- */}
                        {pendingQuests.map((quest) => (
                            <SwipeableRow
                                key={quest.id}
                                onSwipeApprove={() => onApprove(quest)}
                                onSwipeReject={() => onReject(quest)}
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
                                        <Button variant="danger" size="sm" className="min-h-[44px] min-w-[44px]" onClick={() => onReject(quest)}>
                                            <XCircle size={18} />
                                        </Button>
                                        <Button variant="primary" size="sm" className="min-h-[44px]" onClick={() => onApprove(quest)}>
                                            <CheckCircle size={18} /> 承認
                                        </Button>
                                    </div>
                                </div>
                            </SwipeableRow>
                        ))}

                        {/* --- アイテム承認リスト (新規追加) --- */}
                        {pendingItems?.map((item: PendingInventory) => (
                            <SwipeableRow
                                key={item.id}
                                onSwipeApprove={() => setItemToConsume(item)}
                            >
                                <div className="bg-white p-3 rounded shadow-sm flex justify-between items-center border-l-4 border-green-400 gap-2">
                                    <div className="min-w-0">
                                        <p className="font-bold text-gray-800 flex items-center gap-2">
                                            <span className="text-sm bg-green-100 text-green-800 px-2 py-0.5 rounded-full flex items-center gap-1">
                                                <Package size={12} /> アイテム
                                            </span>
                                            {item.title}
                                        </p>
                                        <p className="text-sm text-gray-500">
                                            申請: {item.user_name} ({new Date(item.used_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })})
                                        </p>
                                    </div>
                                    <div className="flex gap-2 flex-shrink-0">
                                        {/* アイテム使用の拒否(キャンセル)は現状APIがないため、一旦承認のみ実装 */}
                                        <Button
                                            variant="primary"
                                            size="sm"
                                            className="min-h-[44px]"
                                            onClick={() => setItemToConsume(item)}
                                            disabled={consumeMutation.isPending}
                                        >
                                            <CheckCircle size={18} /> OK
                                        </Button>
                                    </div>
                                </div>
                            </SwipeableRow>
                        ))}
                    </div>
                </div>
            )}

            {itemToConsume && (
                <Modal isOpen={true} onClose={() => setItemToConsume(null)} title="使用の承認">
                    <div className="flex flex-col gap-4">
                        <p className="text-gray-700">
                            {itemToConsume.user_name}くんの「{itemToConsume.title}」使用を承認しますか？
                        </p>
                        <div className="flex gap-4">
                            <Button variant="secondary" onClick={() => setItemToConsume(null)} className="flex-1">
                                キャンセル
                            </Button>
                            <Button
                                variant="primary"
                                className="flex-1"
                                onClick={() => {
                                    consumeMutation.mutate(itemToConsume.id);
                                    setItemToConsume(null);
                                }}
                            >
                                承認する
                            </Button>
                        </div>
                    </div>
                </Modal>
            )}
        </div>
    );
};

export default ApprovalList;
