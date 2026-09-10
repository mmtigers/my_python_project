// family-quest/src/lib/outOfScopeReload.ts

// #591: vite-plugin-pwaのService Workerのスコープはvite.config.tsのbase(`/quest/`)
// に閉じている。main.tsxのcontrollerchangeハンドラ(#362)は新しいSWが有効化された
// 時点で自動リロードする仕組みだが、`/camera`のようにスコープ外のパスでは、その
// ページ自体がSWの管理下に一切入らない(navigator.serviceWorker.controllerが常に
// nullのまま)ため、controllerchangeイベント自体が発火しない。Echo Show等の常時
// 表示端末で`/camera`を長時間表示したままバックエンドを再デプロイすると、#362が
// 防ごうとした「旧バンドルのまま固着する」問題が/cameraでは未対策のまま残る。
//
// SWの管理外にあるページに対しては、ページ自身のHTMLを定期的にno-cacheで
// 再取得しLast-Modifiedヘッダの変化を見ることで、同じ問題を防ぐ。

import { getPathSegments } from './pathSegments';

/** 現在のパスがService Workerのスコープ(`/quest/`)外かどうかを判定する。 */
export function isOutsideServiceWorkerScope(pathname: string): boolean {
    const segments = getPathSegments(pathname);
    return segments[0] !== 'quest';
}

export interface UpdateCheckDeps {
    fetch: typeof fetch;
    reload: () => void;
    onError?: (error: unknown) => void;
}

/**
 * `pathname`のHTMLを定期呼び出しのたびにno-cacheで再取得し、Last-Modifiedが
 * 前回から変化していれば`deps.reload()`を呼ぶチェック関数を生成する。
 * 初回呼び出しでは比較対象が無いため、基準値を記録するのみでreloadしない。
 */
export function createUpdateChecker(pathname: string, deps: UpdateCheckDeps) {
    let lastModified: string | null = null;

    return async function checkForUpdate(): Promise<void> {
        let current: string | null;
        try {
            const res = await deps.fetch(pathname, { cache: 'no-store' });
            current = res.headers.get('Last-Modified');
        } catch (error) {
            deps.onError?.(error);
            return;
        }

        if (lastModified !== null && current !== null && current !== lastModified) {
            deps.reload();
            return;
        }
        // current === null(Last-Modifiedヘッダが無い応答。例: デプロイの瞬間に
        // index.htmlが一時的に見つからず、ヘッダ無しのエラー応答が返るケース)の
        // 場合、既知のベースラインをnullで上書きしてはならない。上書きすると、
        // 次回ポーリングで新しいビルドのLast-Modifiedを取得しても
        // 「ベースライン不明」として扱われ、変化を検知できずreloadが発火しない
        // (#591が防ぐはずだった「旧バンドル固着」を再発させる。コードレビュー指摘)。
        if (current !== null) {
            lastModified = current;
        }
    };
}
