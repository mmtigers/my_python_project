import React from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../../../lib/apiClient';
import { Card } from '../../../components/ui/Card';
import { Button } from '../../../components/ui/Button';
import { useSound } from '../../../hooks/useSound';
import { Loader2, PackageOpen, Clock, AlertCircle } from 'lucide-react';

type InventoryItem = {
    id: number;
    title: string;
    icon: string;
    desc: string;
    status: 'owned' | 'pending' | 'consumed';
    purchased_at: string;
};

type Props = {
    userId: string;
};

export const InventoryList: React.FC<Props> = ({ userId }) => {
    const queryClient = useQueryClient();
    const { play } = useSound();

    // データ取得
    const { data: items, isLoading } = useQuery({
        queryKey: ['inventory', userId],
        queryFn: () => apiClient.fetchInventory(userId),
        refetchInterval: 5000
    });

    const useMutationAction = useMutation({
        mutationFn: (inventoryId: number) => apiClient.useItem(userId, inventoryId),
        onSuccess: (data) => {
            queryClient.invalidateQueries({ queryKey: ['inventory', userId] });
            // 即時消費か承認待ちかで音を変える
            if (data.status === 'consumed') {
                play('clear'); // ★ここを修正 (quest_clear -> clear)
            } else {
                play('select');
            }
        }
    });

    const cancelMutation = useMutation({
        mutationFn: (inventoryId: number) => apiClient.cancelItemUsage(userId, inventoryId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['inventory', userId] });
            play('cancel');
        }
    });

    if (isLoading) return (
        <div className="flex justify-center items-center py-10 text-slate-400">
            <Loader2 className="animate-spin mr-2" /> 読み込み中...
        </div>
    );

    if (!items || items.length === 0) {
        return (
            <div className="text-center p-8 bg-white/50 rounded-xl border-2 border-dashed border-slate-300">
                <div className="text-6xl mb-4 opacity-50">🎒</div>
                <h3 className="text-lg font-bold text-slate-600 mb-2">まだなにも持っていません</h3>
                <p className="text-sm text-slate-500">
                    「ごほうび」タブで、ためたゴールドを使って<br />
                    アイテムをゲットしよう！
                </p>
            </div>
        );
    }

    return (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pb-20">
            {items.map((item: InventoryItem) => {
                const isPending = item.status === 'pending';

                return (
                    <Card
                        key={item.id}
                        className={`relative overflow-hidden transition-all duration-300 transform hover:scale-[1.02] ${isPending
                                ? 'bg-amber-50 border-amber-300 shadow-amber-100 ring-2 ring-amber-200'
                                : 'bg-white border-slate-200 shadow-sm hover:shadow-md'
                            }`}
                    >
                        {/* 背景装飾 */}
                        <div className="absolute -right-4 -top-4 text-9xl opacity-5 select-none pointer-events-none">
                            {item.icon}
                        </div>

                        <div className="relative z-10 p-1">
                            {/* ヘッダー: アイコンとタイトル */}
                            <div className="flex items-start gap-3 mb-3">
                                <div className={`
                                    text-4xl w-16 h-16 flex items-center justify-center rounded-2xl shadow-inner
                                    ${isPending ? 'bg-amber-100' : 'bg-slate-100'}
                                `}>
                                    {item.icon}
                                </div>
                                <div className="flex-1 min-w-0 pt-1">
                                    <h3 className="font-bold text-lg text-slate-800 leading-tight truncate">
                                        {item.title}
                                    </h3>
                                    <p className="text-xs text-slate-500 mt-1 line-clamp-2">
                                        {item.desc || '説明はありません'}
                                    </p>
                                    <p className="text-[10px] text-slate-400 mt-2 flex items-center gap-1">
                                        <Clock size={10} />
                                        購入: {new Date(item.purchased_at).toLocaleDateString()}
                                    </p>
                                </div>
                            </div>

                            {/* フッター: アクション */}
                            <div className="mt-2 pt-2 border-t border-slate-100/50">
                                {item.status === 'owned' && (
                                    <Button
                                        size="md"
                                        className="w-full bg-gradient-to-r from-blue-500 to-blue-600 hover:from-blue-600 hover:to-blue-700 text-white shadow-md active:scale-95 transition-all"
                                        onClick={() => {
                                            if (confirm(`「${item.title}」を使いますか？\n（パパ・ママに通知がいきます）`)) {
                                                useMutationAction.mutate(item.id);
                                            }
                                        }}
                                        disabled={useMutationAction.isPending}
                                    >
                                        <PackageOpen size={18} className="mr-2" />
                                        つかう！
                                    </Button>
                                )}

                                {isPending && (
                                    <div className="flex items-center justify-between bg-amber-100/50 p-2 rounded-lg">
                                        <div className="flex items-center gap-2 text-amber-700 text-sm font-bold animate-pulse">
                                            <AlertCircle size={16} />
                                            <span>承認待ち...</span>
                                        </div>
                                        <button
                                            className="text-xs text-slate-400 underline hover:text-slate-600 px-2 py-1"
                                            onClick={() => cancelMutation.mutate(item.id)}
                                        >
                                            やめる
                                        </button>
                                    </div>
                                )}
                            </div>
                        </div>
                    </Card>
                );
            })}
        </div>
    );
};