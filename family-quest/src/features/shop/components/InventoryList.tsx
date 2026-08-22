import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../../../lib/apiClient';
import { Card } from '../../../components/ui/Card';
import { Button } from '../../../components/ui/Button';
import { Modal } from '../../../components/ui/Modal';
import { useSound } from '../../../hooks/useSound';
import { useToast } from '../../../context/useToast';
import { Loader2, PackageOpen, AlertCircle } from 'lucide-react';
import { InventoryItem } from '../../../types';

// M-6-3: apiClient側でスローされるErrorのmessageには、バックエンドが返す
// {"detail": "..."} の内容が入っている(apiClient.ts参照)。
const extractErrorDetail = (error: unknown): string => {
    return error instanceof Error && error.message ? error.message : '操作に失敗しました';
};



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
    const { showToast } = useToast();
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
            const usedInventoryId = variables; // 使用申請したアイテムID

            // H-5: use_itemはバックエンド側で即時消費(consumed)ではなく承認待ち
            // (pending)にするため、リストから消さずステータスのみ更新する。
            // 実際の消費確定(quest_historyへの記録・chronicle反映)は親の承認
            // (consume_item)時に行われる。
            queryClient.setQueryData<InventoryItem[]>(queryKey, (oldItems) => {
                if (!oldItems) return [];
                return oldItems.map(item =>
                    item.id === usedInventoryId ? { ...item, status: 'pending' } : item
                );
            });

            // 念のためサーバーとも同期(承認待ち一覧のポーリングにも反映される)
            queryClient.invalidateQueries({ queryKey: queryKey });
            queryClient.invalidateQueries({ queryKey: ['pendingInventory'] });

            // 承認待ちになったことを示す申請音(quest完了時のpending相当)を再生
            play('submit');
        },
        // M-6-3: 以前はonErrorが無く、使用申請の失敗(通信エラー等)が
        // ユーザーに一切通知されないサイレント失敗になっていた。
        onError: (error) => {
            showToast({ title: "エラー", text: extractErrorDetail(error), icon: "⚠️" });
            play('cancel');
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
        },
        onError: (error) => {
            showToast({ title: "エラー", text: extractErrorDetail(error), icon: "⚠️" });
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
    // panelMode時は常に1カラムにする。
    const gridClass = panelMode ? 'grid-cols-1' : 'grid-cols-1 sm:grid-cols-2';
    const iconBoxClass = panelMode ? 'text-xl w-9 h-9' : 'text-2xl w-11 h-11';

    return (
        <div className={`grid ${gridClass} gap-2 pb-20`}>
            {items.map((item: InventoryItem) => {
                const isPending = item.status === 'pending';
                const isOwned = item.status === 'owned';

                return (
                    <Card
                        key={item.id}
                        // ★バグ修正: 「つかう」ボタンを廃止し、カード自体をタップしたら
                        // つかう確認モーダルを開くようにする(1行のコンパクト表示にするため)
                        onClick={isOwned ? () => setItemToUse(item) : undefined}
                        className={`flex items-center gap-2 p-2 transition-all ${isPending
                            ? 'bg-amber-50 border-amber-300 ring-2 ring-amber-200'
                            : 'bg-white border-slate-200 shadow-sm hover:shadow-md active:scale-[0.98] cursor-pointer'
                            }`}
                    >
                        <div className={`
                            ${iconBoxClass} flex items-center justify-center rounded-xl flex-shrink-0
                            ${isPending ? 'bg-amber-100' : 'bg-slate-100'}
                        `}>
                            {item.icon}
                        </div>
                        <div className="flex-1 min-w-0">
                            <h3 className="font-bold text-sm text-slate-800 leading-tight truncate">
                                {item.title}
                            </h3>
                            <p className="text-[10px] text-slate-500 truncate">
                                {item.desc || '説明はありません'}
                            </p>
                        </div>

                        {isPending ? (
                            <div className="flex flex-col items-end gap-0.5 flex-shrink-0">
                                <span className="text-[10px] text-amber-700 font-bold flex items-center gap-0.5 animate-pulse">
                                    <AlertCircle size={11} />承認待ち
                                </span>
                                <button
                                    className="min-h-[32px] text-[10px] text-slate-500 underline hover:text-slate-700 px-2"
                                    onClick={(e) => { e.stopPropagation(); cancelMutation.mutate(item.id); }}
                                    disabled={cancelMutation.isPending}
                                >
                                    やめる
                                </button>
                            </div>
                        ) : (
                            <PackageOpen size={18} className="text-blue-500 flex-shrink-0" />
                        )}
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