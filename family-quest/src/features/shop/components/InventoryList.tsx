import React from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../../../lib/apiClient';

// ▼ 修正箇所: { } をつけて Named Import に変更してください
import { Card } from '../../../components/ui/Card';
import { Button } from '../../../components/ui/Button';
import { useSound } from '../../../hooks/useSound';

type InventoryItem = {
    id: number;
    title: string;
    icon: string;
    desc: string;
    status: 'owned' | 'pending' | 'consumed';
    purchased_at: string;
};

// ... 以下、変更なし ...
type Props = {
    userId: string;
};

export const InventoryList: React.FC<Props> = ({ userId }) => {
    // ... 実装内容はそのまま ...
    const queryClient = useQueryClient();
    const { play } = useSound(); // ※もしuseSoundの戻り値がオブジェクトなら分割代入が必要ですが、通常は play関数か { play } です。
    // もしエラーが出る場合は const playSound = useSound(); のままでOKかどうか確認が必要です。
    // エラーログにはuseSound自体のインポートエラーしか出ていないので、まずはimport修正だけでOKです。

    // データ取得
    const { data: items, isLoading } = useQuery({
        queryKey: ['inventory', userId],
        queryFn: () => apiClient.fetchInventory(userId),
        refetchInterval: 5000
    });

    const useMutationAction = useMutation({
        mutationFn: (inventoryId: number) => apiClient.useItem(userId, inventoryId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['inventory', userId] });
            play('select');  // ★ここを追加（またはコメントアウト解除）
        }
    });

    const cancelMutation = useMutation({
        mutationFn: (inventoryId: number) => apiClient.cancelItemUsage(userId, inventoryId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['inventory', userId] });
            play('cancel');
        }
    });

    // ... レンダリング部分 ...
    if (isLoading) return <div className="text-center p-4">読み込み中...</div>;
    if (!items || items.length === 0) {
        return (
            <div className="text-center p-8 text-gray-500 bg-white/50 rounded-xl">
                <p className="text-4xl mb-2">🎒</p>
                <p>まだなにも持っていません</p>
                <p className="text-sm mt-2">お店でチケットを買ってみよう！</p>
            </div>
        );
    }

    return (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {items.map((item: InventoryItem) => (
                <Card key={item.id} className={`relative overflow-hidden transition-all ${item.status === 'pending' ? 'bg-yellow-50 border-yellow-300 ring-2 ring-yellow-200' : ''
                    }`}>
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                            <div className="text-4xl bg-gray-100 p-2 rounded-full">{item.icon}</div>
                            <div>
                                <h3 className="font-bold text-gray-800">{item.title}</h3>
                                <p className="text-xs text-gray-500">{item.desc}</p>
                                <p className="text-[10px] text-gray-400 mt-1">
                                    購入日: {new Date(item.purchased_at).toLocaleDateString()}
                                </p>
                            </div>
                        </div>

                        <div className="flex flex-col gap-2">
                            {item.status === 'owned' && (
                                <Button
                                    size="sm"
                                    variant="primary"
                                    onClick={() => {
                                        if (confirm(`「${item.title}」を使いますか？\n（パパ・ママに通知がいきます）`)) {
                                            useMutationAction.mutate(item.id);
                                        }
                                    }}
                                    disabled={useMutationAction.isPending}
                                >
                                    つかう！
                                </Button>
                            )}

                            {item.status === 'pending' && (
                                <div className="flex flex-col items-end gap-1">
                                    <span className="px-2 py-1 bg-yellow-100 text-yellow-700 text-xs font-bold rounded-full animate-pulse">
                                        承認待ち...
                                    </span>
                                    <button
                                        className="text-xs text-gray-400 underline hover:text-gray-600"
                                        onClick={() => cancelMutation.mutate(item.id)}
                                    >
                                        やめる
                                    </button>
                                </div>
                            )}
                        </div>
                    </div>
                </Card>
            ))}
        </div>
    );
};