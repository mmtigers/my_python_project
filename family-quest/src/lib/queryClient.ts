import { QueryClient } from '@tanstack/react-query';

export const queryClient = new QueryClient({
    defaultOptions: {
        queries: {
            retry: 1,
            staleTime: 1000 * 60,
            // #803: 以前は false(フォーカス復帰で再取得しない)だった。React Query 移行時
            // (8ec87bf2)に入ったもので、無効化した理由はコミットにもコメントにも残っていない。
            // モバイルのバックグラウンドタブは refetchInterval のタイマーごと凍結されるため、
            // 「タブを離れる → タイマー停止 → タブに戻っても再取得しない」という経路が残り、
            // 復帰直後に古いデータが表示され続けていた(実機で1時間半前の残高が出た)。
            // React Query の既定値である true へ戻し、復帰時に1回だけ取りに行く。
            // staleTime 内のクエリは再取得されないため、タブの往復が多くてもリクエストは
            // 増えすぎない(gameData は 30 秒、年代記は 5 分、ルーティンは 10 秒)。
            refetchOnWindowFocus: true,
        },
    },
});
