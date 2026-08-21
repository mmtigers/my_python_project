import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../../../lib/apiClient';
import { Card } from '../../../components/ui/Card';
import { Button } from '../../../components/ui/Button';
import { Modal } from '../../../components/ui/Modal';
import { useSound } from '../../../hooks/useSound';
import { Loader2, PackageOpen, Clock, AlertCircle } from 'lucide-react';
import { InventoryItem } from '../../../types';



type Props = {
    userId: string;
    // PC横画面の4人並びパネルなど、実際の表示幅が狭い枠内に埋め込む場合に指定する。
    // 通常の sm:grid-cols-2 はブラウザの「ビューポート幅」基準のため、狭いパネルに
    // 埋め込まれていても(ビューポート自体は広いPC画面なので)2カラム化してしまい、
    // アイコン・ボタンが見切れる原因になっていた。panelMode時は常に1カラムにする。
    panelMode?: boolean;
};

export const InventoryList: React.FC<Props> = ({ userId, panelMode }) => {
    const queryClient = useQueryClient();
    const { play } = useSound();
    const queryKey = ['inventory', userId]; // QueryKeyを定数化

    // ★変更: 素の confirm() を廃止し、アプリ標準の Modal で「つかう」確認を行う
    const [itemToUse, setItemToUse] = useState<InventoryItem | null>(null);

    // データ取得
    const { data: items, isLoading } = useQuery({
        queryKey: queryKey,
        queryFn: () => apiClient.fetchInventory(userId),
        refetchInterval: 5000
    });

    const useMutationAction = useMutation({
        mutationFn: (inventoryId: number) => apiClient.useItem(userId, inventoryId),
        onSuccess: (_data, variables) => {
            const usedInventoryId = variables; // 使用したアイテムID

            // ★追加: 即時反映処理 (Optimistic Update like behavior)
            // サーバーからの応答を待たず、または応答直後にキャッシュを書き換えてアイテムを消す
            queryClient.setQueryData<InventoryItem[]>(queryKey, (oldItems) => {
                if (!oldItems) return [];
                // consumed(使用済み)になったアイテムをリストから除外
                return oldItems.filter(item => item.id !== usedInventoryId);
            });

            // 念のためサーバーとも同期
            queryClient.invalidateQueries({ queryKey: queryKey });

            // ★変更: 承認不要なので常にクリア音を再生
            play('clear');
        }
    });

    const cancelMutation = useMutation({
        mutationFn: (inventoryId: number) => apiClient.cancelItemUsage(userId, inventoryId),
        onSuccess: (_data, variables) => {
            // キャンセル時も即座にステータスを戻す
            const targetId = variables;
            queryClient.setQueryData<InventoryItem[]>(queryKey, (oldItems) => {
                if (!oldItems) return [];
                return oldItems.map(item =>
                    item.id === targetId ? { ...item, status: 'owned' } : item
                );
            });
            queryClient.invalidateQueries({ queryKey: queryKey });
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

    // panelMode(PC横画面の4人並びパネル内)では、実際に使える幅が狭い(約300px)。
    // sm:grid-cols-2 はビューポート幅基準のため、狭いパネルに埋め込まれていても
    // (ビューポート自体はPCなので)2カラム化してアイコン・ボタンが見切れてしまう。
    // panelMode時は常に1カラム・コンパクトな寸法にする。
    const gridClass = panelMode ? 'grid-cols-1 gap-2' : 'grid-cols-1 sm:grid-cols-2 gap-4';
    const iconBoxClass = panelMode ? 'text-2xl w-10 h-10' : 'text-4xl w-16 h-16';
    const titleClass = panelMode ? 'text-sm' : 'text-lg';

    return (
        <div className={`grid ${gridClass} pb-20`}>
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
                        {/* 背景装飾: 狭いパネルでは見た目のノイズになるため非表示にする */}
                        {!panelMode && (
                            <div className="absolute -right-4 -top-4 text-9xl opacity-5 select-none pointer-events-none">
                                {item.icon}
                            </div>
                        )}

                        <div className="relative z-10 p-1">
                            {/* ヘッダー: アイコンとタイトル */}
                            <div className="flex items-start gap-3 mb-3">
                                <div className={`
                                    ${iconBoxClass} flex items-center justify-center rounded-2xl shadow-inner flex-shrink-0
                                    ${isPending ? 'bg-amber-100' : 'bg-slate-100'}
                                `}>
                                    {item.icon}
                                </div>
                                <div className="flex-1 min-w-0 pt-1">
                                    <h3 className={`font-bold ${titleClass} text-slate-800 leading-tight truncate`}>
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
                                        className="w-full min-h-[44px] bg-gradient-to-r from-blue-500 to-blue-600 hover:from-blue-600 hover:to-blue-700 text-white shadow-md active:scale-95 transition-all"
                                        onClick={() => {
                                            // ★変更: 「パパ・ママに通知がいきます」の文言を削除
                                            setItemToUse(item);
                                        }}
                                        disabled={useMutationAction.isPending}
                                    >
                                        <PackageOpen size={18} className="mr-2" />
                                        つかう！
                                    </Button>
                                )}

                                {/* 既存機能維持: 過去データ等でpendingのものがあれば表示する */}
                                {isPending && (
                                    <div className="flex items-center justify-between bg-amber-100/50 p-2 rounded-lg">
                                        <div className="flex items-center gap-2 text-amber-700 text-sm font-bold animate-pulse">
                                            <AlertCircle size={16} />
                                            <span>承認待ち...</span>
                                        </div>
                                        <button
                                            className="min-h-[44px] min-w-[44px] text-xs text-slate-500 underline hover:text-slate-700 px-3"
                                            onClick={() => cancelMutation.mutate(item.id)}
                                            disabled={cancelMutation.isPending}
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

            <Modal
                isOpen={!!itemToUse}
                onClose={() => setItemToUse(null)}
                title="つかう"
                footer={
                    <>
                        <Button variant="secondary" onClick={() => setItemToUse(null)}>キャンセル</Button>
                        <Button
                            variant="primary"
                            onClick={() => {
                                if (itemToUse) useMutationAction.mutate(itemToUse.id);
                                setItemToUse(null);
                            }}
                        >
                            はい
                        </Button>
                    </>
                }
            >
                <p className="text-center">「{itemToUse?.title}」を使いますか？</p>
            </Modal>
        </div>
    );
};