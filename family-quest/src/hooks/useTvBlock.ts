import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '../lib/apiClient';
import { TV_BLOCK_POLL_INTERVAL_MS } from '../lib/uiConstants';
import { TvBlockState } from '../types';

// 休日のテレビおやすみ(禁止時間帯)の「禁止開始までの秒数」を、サーバー値を起点に
// ローカルで1秒ずつ減らすカウントダウン。ごほうび画面(InventoryList)と画面上部の
// 帯(TvBlockHeaderBanner)で判定を食い違わせないよう、ここに集約している。
export function useTvBlockCountdown(tvBlock: TvBlockState | null) {
    const serverValue = tvBlock?.seconds_until_next_block ?? null;
    const [tvSecondsLeft, setTvSecondsLeft] = useState<number | null>(null);
    useEffect(() => {
        // クールダウンと同じ理由(サーバー値を起点にしたローカルカウントダウン)で意図的。
        // eslint-disable-next-line react-hooks/set-state-in-effect -- 上記の理由で意図的
        setTvSecondsLeft(serverValue);
        if (serverValue === null) return;

        const id = window.setInterval(() => {
            setTvSecondsLeft((s) => (s === null ? s : Math.max(0, s - 1)));
        }, 1000);
        return () => window.clearInterval(id);
    }, [serverValue]);

    // 禁止開始までの秒数が0になった直後〜次のポーリングまでは「おやすみ中」として扱う。
    const tvBlockActive = (tvBlock?.is_blocked ?? false) || (tvSecondsLeft !== null && tvSecondsLeft <= 0);
    return { tvSecondsLeft, tvBlockActive };
}

// 全員共通の状態を取得する(ユーザーに依らないため、ごほうび画面のように user_id は不要)。
// 取得に失敗しても帯を出さないだけで、画面の他の部分には影響させない。
export function useTvBlock() {
    const { data } = useQuery({
        queryKey: ['tvBlock'],
        queryFn: () => apiClient.fetchTvBlock(),
        refetchInterval: TV_BLOCK_POLL_INTERVAL_MS,
    });
    const tvBlock = data ?? null;
    return { tvBlock, ...useTvBlockCountdown(tvBlock) };
}
