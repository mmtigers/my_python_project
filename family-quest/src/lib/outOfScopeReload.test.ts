import { describe, expect, it, vi } from 'vitest';
import { createUpdateChecker, isOutsideServiceWorkerScope } from './outOfScopeReload';

// Issue #591: /camera はService Workerのスコープ(/quest/)外のため、main.tsxの
// controllerchangeによる自動リロード(#362)が発火しない。代替の定期チェック
// (Last-Modifiedの変化検知)の回帰テスト。

describe('isOutsideServiceWorkerScope (#591)', () => {
    it.each(['/camera', '/camera/', '/camera/live/cam1', '/', '/settings'])(
        'treats %s as outside the service worker scope',
        (pathname) => {
            expect(isOutsideServiceWorkerScope(pathname)).toBe(true);
        }
    );

    it.each(['/quest', '/quest/', '/quest/camera', '/quest/settings'])(
        'treats %s as inside the service worker scope (base: /quest/)',
        (pathname) => {
            expect(isOutsideServiceWorkerScope(pathname)).toBe(false);
        }
    );
});

describe('createUpdateChecker (#591)', () => {
    function fakeFetch(lastModified: string | null) {
        return vi.fn().mockResolvedValue({
            headers: { get: (name: string) => (name === 'Last-Modified' ? lastModified : null) },
        });
    }

    it('does not reload on the first check (no baseline to compare against yet)', async () => {
        const reload = vi.fn();
        const checker = createUpdateChecker('/camera', { fetch: fakeFetch('Mon, 01 Jan 2026 00:00:00 GMT'), reload });

        await checker();

        expect(reload).not.toHaveBeenCalled();
    });

    it('does not reload when Last-Modified is unchanged across checks', async () => {
        const reload = vi.fn();
        const fetchImpl = fakeFetch('Mon, 01 Jan 2026 00:00:00 GMT');
        const checker = createUpdateChecker('/camera', { fetch: fetchImpl, reload });

        await checker();
        await checker();
        await checker();

        expect(reload).not.toHaveBeenCalled();
    });

    it('reloads once Last-Modified changes from the recorded baseline', async () => {
        const reload = vi.fn();
        let current = 'Mon, 01 Jan 2026 00:00:00 GMT';
        const fetchImpl = vi.fn().mockImplementation(() =>
            Promise.resolve({ headers: { get: () => current } })
        );
        const checker = createUpdateChecker('/camera', { fetch: fetchImpl, reload });

        await checker(); // establishes baseline
        expect(reload).not.toHaveBeenCalled();

        current = 'Tue, 02 Jan 2026 00:00:00 GMT'; // backend was redeployed
        await checker();

        expect(reload).toHaveBeenCalledTimes(1);
    });

    it('fetches the given pathname with cache disabled', async () => {
        const fetchImpl = fakeFetch('Mon, 01 Jan 2026 00:00:00 GMT');
        const checker = createUpdateChecker('/camera', { fetch: fetchImpl, reload: vi.fn() });

        await checker();

        expect(fetchImpl).toHaveBeenCalledWith('/camera', { cache: 'no-store' });
    });

    it('reports fetch failures via onError instead of throwing, and does not reload', async () => {
        const reload = vi.fn();
        const onError = vi.fn();
        const fetchImpl = vi.fn().mockRejectedValue(new Error('network down'));
        const checker = createUpdateChecker('/camera', { fetch: fetchImpl, reload, onError });

        await checker();

        expect(onError).toHaveBeenCalledTimes(1);
        expect(reload).not.toHaveBeenCalled();
    });

    it('does not reload when Last-Modified is missing from the response', async () => {
        const reload = vi.fn();
        const fetchImpl = fakeFetch(null);
        const checker = createUpdateChecker('/camera', { fetch: fetchImpl, reload });

        await checker();
        await checker();

        expect(reload).not.toHaveBeenCalled();
    });

    it('does not lose the known baseline when a single poll is missing Last-Modified, and still detects a later change', async () => {
        // コードレビュー指摘の回帰テスト: デプロイの瞬間にindex.htmlが一時的に
        // 見つからずLast-Modified無しの応答が挟まっても、既知のベースラインを
        // nullで上書きしてはならない。そうしないと、直後に新しいビルドを
        // 検知してもベースライン不明として再学習するだけでreloadが発火しない。
        const reload = vi.fn();
        const responses: (string | null)[] = [
            'Mon, 01 Jan 2026 00:00:00 GMT', // 通常のポーリング(ベースライン確立)
            null, // デプロイ中の瞬断: Last-Modifiedヘッダ無しの応答
            'Tue, 02 Jan 2026 00:00:00 GMT', // デプロイ完了後の新しいビルド
        ];
        const fetchImpl = vi.fn().mockImplementation(() =>
            Promise.resolve({ headers: { get: () => responses.shift() ?? null } })
        );
        const checker = createUpdateChecker('/camera', { fetch: fetchImpl, reload });

        await checker(); // baseline: 'Mon...'
        expect(reload).not.toHaveBeenCalled();

        await checker(); // Last-Modified欠落。baselineは'Mon...'のまま維持されるべき
        expect(reload).not.toHaveBeenCalled();

        await checker(); // 'Tue...'を検知し、'Mon...'との差分でreloadが発火するべき
        expect(reload).toHaveBeenCalledTimes(1);
    });
});
