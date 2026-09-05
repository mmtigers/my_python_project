import React, { useEffect, useRef, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../../../lib/apiClient';
import { Card } from '../../../components/ui/Card';
import { Button } from '../../../components/ui/Button';
import { Modal } from '../../../components/ui/Modal';
import { useSound } from '../../../hooks/useSound';
import { useToast } from '../../../context/useToast';
import { Loader2, PackageOpen, Lock } from 'lucide-react';
import { InventoryItem, InventoryResponse } from '../../../types';
// M-6-3/#412(品質): apiClient側でスローされるErrorのmessageには、バックエンドが返す
// {"detail": "..."} の内容が入っている(apiClient.ts参照)。以前はこのファイルと
// CameraDashboard.tsxにほぼ同じ関数が重複していたため lib/errorDetail.ts に集約した。
import { extractErrorDetail } from '../../../lib/errorDetail';

type Props = {
    userId: string;
    // PC横画面の4人並びパネルなど、実際の表示幅が狭い枠内に埋め込む場合に指定する。
    // 通常の sm:grid-cols-2 はブラウザの「ビューポート幅」基準のため、狭いパネルに
    // 埋め込まれていても(ビューポート自体は広いPC画面なので)2カラム化してしまい、
    // アイコン・ボタンが見切れる原因になっていた。panelMode時は常に1カラムにする。
    panelMode?: boolean;
};

// 秒数を "あと13:05" のようなmm:ss表記にする(YouTubeごほうび券クールダウン表示用)。
function formatCooldown(totalSeconds: number): string {
    const m = Math.floor(totalSeconds / 60);
    const s = totalSeconds % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
}

// "2026-09-12" のようなISO日付を "9/12(土)" のような表示用文字列にする
// (YouTubeごほうび券クールダウンの予告バナー用)。
function formatAnnouncementDate(isoDate: string): string {
    const date = new Date(`${isoDate}T00:00:00`);
    return date.toLocaleDateString('ja-JP', { month: 'numeric', day: 'numeric', weekday: 'short' });
}

export const InventoryList: React.FC<Props> = ({ userId, panelMode }) => {
    const queryClient = useQueryClient();
    const { play } = useSound();
    const { showToast } = useToast();
    const queryKey = ['inventory', userId]; // QueryKeyを定数化

    // ★変更: 素の confirm() を廃止し、アプリ標準の Modal で「つかう」確認を行う
    const [itemToUse, setItemToUse] = useState<InventoryItem | null>(null);
    // #119: 「はい」の連打(ダブルタップ)による同一アイテムへの多重使用リクエストを防ぐガード。
    // モーダルを閉じるsetItemToUse(null)による再レンダーを待たずに2回目のクリックが
    // 発火しうるため(#101のisConfirmingRefと同じ理由)、reactiveなuseMutationAction.isPending
    // ではなくrefで同期的に判定する。
    const isUsingItemRef = useRef(false);

    // データ取得
    const { data, isLoading, isError, error } = useQuery({
        queryKey: queryKey,
        queryFn: () => apiClient.fetchInventory(userId),
        refetchInterval: 5000
    });
    const items = data?.items;
    const cooldownAnnouncement = data?.youtube_cooldown_announcement ?? null;

    // YouTube系ごほうび券の連続使用防止クールダウン(15分)の残り秒数。
    // サーバー値(5秒間隔のポーリングで再同期)を起点に、表示だけ1秒間隔でローカルに
    // カウントダウンする(QuestList.tsxのCooldownRingと同様の考え方)。
    const [youtubeCooldownSeconds, setYoutubeCooldownSeconds] = useState(0);
    useEffect(() => {
        const serverValue = data?.youtube_cooldown_remaining_seconds ?? 0;
        setYoutubeCooldownSeconds(serverValue);
        if (serverValue <= 0) return;

        const id = window.setInterval(() => {
            setYoutubeCooldownSeconds((s) => Math.max(0, s - 1));
        }, 1000);
        return () => window.clearInterval(id);
    }, [data?.youtube_cooldown_remaining_seconds]);

    // #441: 他のクエリ/ミューテーションは軒並みエラー通知(トースト)が実装済みだが、
    // 一覧取得自体にはエラーハンドリングが無く、取得失敗時に画面上は何も表示されない
    // サイレント失敗のままだった(「アイテムが無い」のか「読み込みに失敗した」のか
    // 区別できない)。失敗が続くポーリングのたびに連投しないよう、エラー状態に
    // 入った最初の1回だけトーストを出す。
    const hasShownFetchErrorRef = useRef(false);
    useEffect(() => {
        if (isError) {
            if (!hasShownFetchErrorRef.current) {
                hasShownFetchErrorRef.current = true;
                showToast({ title: "エラー", text: extractErrorDetail(error, 'アイテム一覧の取得に失敗しました'), icon: "⚠️" });
            }
        } else {
            hasShownFetchErrorRef.current = false;
        }
    }, [isError, error, showToast]);

    const useMutationAction = useMutation({
        mutationFn: (inventoryId: number) => apiClient.useItem(userId, inventoryId),
        onSuccess: (_data, variables) => {
            // アイテム使用は即座に消費が確定する(親の承認は不要)ため、
            // リストからも即座に取り除く。
            const usedInventoryId = variables;
            queryClient.setQueryData<InventoryResponse>(queryKey, (old) => {
                if (!old) return old;
                return { ...old, items: old.items.filter(item => item.id !== usedInventoryId) };
            });

            // 念のためサーバーとも同期
            queryClient.invalidateQueries({ queryKey: queryKey });
            queryClient.invalidateQueries({ queryKey: ['chronicle'] });

            play('clear');
        },
        // M-6-3: 以前はonErrorが無く、使用申請の失敗(通信エラー等)が
        // ユーザーに一切通知されないサイレント失敗になっていた。
        onError: (error) => {
            showToast({ title: "エラー", text: extractErrorDetail(error, '操作に失敗しました'), icon: "⚠️" });
            play('cancel');
        },
        onSettled: () => {
            isUsingItemRef.current = false;
        }
    });

    if (isLoading) return (
        <div className="flex justify-center items-center py-10 text-slate-400">
            <Loader2 className="animate-spin mr-2" /> 読み込み中...
        </div>
    );

    // YouTubeごほうび券クールダウンの猶予期間中(実際の制限開始前)の予告バナー。
    // いきなり制限がかかると子どもが困惑するため、施行日を迎えるまで表示しておく。
    const announcementBanner = cooldownAnnouncement && (
        <div className="flex items-start gap-2 p-3 rounded-xl bg-amber-50 border border-amber-300 text-amber-800 text-xs">
            <span className="text-base leading-none flex-shrink-0">📢</span>
            <p>
                <span className="font-bold">{formatAnnouncementDate(cooldownAnnouncement.starts_on)}から</span>、
                YouTubeのごほうび券は使うと15分間、次の1枚が使えなくなります(目を休めるため)。
            </p>
        </div>
    );

    if (!items || items.length === 0) {
        return (
            <div className="flex flex-col gap-3">
                {announcementBanner}
                <div className="text-center p-8 bg-white/50 rounded-xl border-2 border-dashed border-slate-300">
                    <div className="text-6xl mb-4 opacity-50">🎒</div>
                    <h3 className="text-lg font-bold text-slate-600 mb-2">まだなにも持っていません</h3>
                    <p className="text-sm text-slate-500">
                        「ごほうび」タブで、ためたゴールドを使って<br />
                        アイテムをゲットしよう！
                    </p>
                </div>
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
            {announcementBanner && (
                <div className="col-span-full">{announcementBanner}</div>
            )}
            {items.map((item: InventoryItem) => {
                // 目の負担を防ぐため、YouTube系ごほうび券は前回使用から15分は使えない。
                const isCoolingDown = item.is_youtube_reward && youtubeCooldownSeconds > 0;

                return (
                    <Card
                        key={item.id}
                        // ★バグ修正: 「つかう」ボタンを廃止し、カード自体をタップしたら
                        // つかう確認モーダルを開くようにする(1行のコンパクト表示にするため)
                        onClick={() => { if (!isCoolingDown) setItemToUse(item); }}
                        className={`flex items-center gap-2 p-2 transition-all border-slate-200 shadow-sm ${
                            isCoolingDown
                                ? 'bg-slate-100 opacity-70 cursor-not-allowed'
                                : 'bg-white hover:shadow-md active:scale-[0.98] cursor-pointer'
                        }`}
                    >
                        <div className={`${iconBoxClass} flex items-center justify-center rounded-xl flex-shrink-0 bg-slate-100`}>
                            {item.icon}
                        </div>
                        <div className="flex-1 min-w-0">
                            <h3 className="font-bold text-sm text-slate-800 leading-tight truncate">
                                {item.title}
                            </h3>
                            <p className="text-[10px] text-slate-500 truncate">
                                {isCoolingDown
                                    ? `目を休めよう。あと${formatCooldown(youtubeCooldownSeconds)}で使えます`
                                    : (item.desc || '説明はありません')}
                            </p>
                        </div>

                        {isCoolingDown ? (
                            <Lock size={18} className="text-slate-400 flex-shrink-0" />
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
                                // #119: 連打(ダブルタップ)で同一アイテムに対する使用リクエストが
                                // 二重送信されると、2回目はサーバー側でstatus!='owned'により
                                // 400「Cannot use this item」となり、実際は成功しているのに
                                // エラートーストが出ていた。isUsingItemRefで同期的に多重送信を防ぐ。
                                if (itemToUse && !isUsingItemRef.current) {
                                    isUsingItemRef.current = true;
                                    useMutationAction.mutate(itemToUse.id);
                                }
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