import React from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { CheckCircle, XCircle, Package } from 'lucide-react'; // Packageアイコン追加
import { QuestHistory, User } from '@/types';
import { Button } from '../../../components/ui/Button';
import { apiClient } from '../../../lib/apiClient';

type Props = {
    pendingQuests: QuestHistory[];
    users: User[];
    onApprove: (history: QuestHistory) => void;
    onReject: (history: QuestHistory) => void;
};

// 承認待ちアイテムの型定義
type PendingInventory = {
    id: number;
    user_id: string;
    user_name: string;
    title: string;
    icon: string;
    used_at: string;
};

const ApprovalList: React.FC<Props> = ({ pendingQuests, users, onApprove, onReject }) => {
    const queryClient = useQueryClient();

    // 1. アイテムの承認待ちリストを取得
    const { data: pendingItems } = useQuery({
        queryKey: ['pendingInventory'],
        queryFn: () => apiClient.fetchPendingInventory(),
        refetchInterval: 5000 // ポーリングで最新状態を維持
    });

    // 2. アイテム承認（消費）アクション
    const consumeMutation = useMutation({
        mutationFn: (inventoryId: number) => apiClient.consumeItem('dad', inventoryId), // 'dad'は仮。App.tsxから親IDを渡すのがベストですが一旦これで動作します
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['pendingInventory'] });
            // 必要に応じて親のインベントリリストなども更新
            queryClient.invalidateQueries({ queryKey: ['inventory'] });
        }
    });

    const getUserName = (userId: string) => {
        return users.find(u => u.user_id === userId)?.name || userId;
    };

    const hasQuests = pendingQuests.length > 0;
    const hasItems = pendingItems && pendingItems.length > 0;

    if (!hasQuests && !hasItems) return null;

    return (
        <div className="bg-yellow-50 border-l-4 border-yellow-400 p-4 mb-4 rounded shadow-sm animate-fade-in">
            <h3 className="font-bold text-yellow-800 mb-2 flex items-center gap-2">
                <span className="animate-pulse">🔔</span> 承認待ちのアクション
            </h3>

            <div className="space-y-3">
                {/* --- クエスト承認リスト (既存) --- */}
                {pendingQuests.map((quest) => (
                    <div key={quest.id} className="bg-white p-3 rounded shadow-sm flex justify-between items-center">
                        <div>
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
                        <div className="flex gap-2">
                            <Button variant="danger" size="sm" onClick={() => onReject(quest)}>
                                <XCircle size={18} />
                            </Button>
                            <Button variant="primary" size="sm" onClick={() => onApprove(quest)}>
                                <CheckCircle size={18} /> 承認
                            </Button>
                        </div>
                    </div>
                ))}

                {/* --- アイテム承認リスト (新規追加) --- */}
                {pendingItems?.map((item: PendingInventory) => (
                    <div key={item.id} className="bg-white p-3 rounded shadow-sm flex justify-between items-center border-l-4 border-green-400">
                        <div>
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
                        <div className="flex gap-2">
                            {/* アイテム使用の拒否(キャンセル)は現状APIがないため、一旦承認のみ実装 */}
                            <Button
                                variant="primary"
                                size="sm"
                                onClick={() => {
                                    if (confirm(`${item.user_name}くんの「${item.title}」使用を承認しますか？`)) {
                                        consumeMutation.mutate(item.id);
                                    }
                                }}
                                disabled={consumeMutation.isPending}
                            >
                                <CheckCircle size={18} /> OK
                            </Button>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
};

export default ApprovalList;