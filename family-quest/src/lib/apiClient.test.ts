import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from './apiClient';

// #412(F-L3): apiClient._request のエラー・空ボディ処理を検証する。
// - 204/空ボディの成功応答で JSON.parse に失敗しない
// - 422の detail 配列(FastAPIのバリデーションエラー)から msg を拾う
// - fetch自体の失敗(TypeError)を意味の伝わる日本語文言に変換する
// - 通常の {"detail": "..."} 応答・通常の成功応答は従来通り

const jsonResponse = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('apiClient error handling (#412 F-L3)', () => {
    afterEach(() => {
        vi.restoreAllMocks();
        vi.unstubAllGlobals();
    });

    it('returns parsed JSON on a normal 200 response', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ ok: true })));
        await expect(apiClient.get('/api/quest/data')).resolves.toEqual({ ok: true });
    });

    it('does not throw "Unexpected end of JSON input" on a 204 No Content response', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
        await expect(apiClient.post('/api/quest/quest/cancel', {})).resolves.toBeUndefined();
    });

    it('does not throw on an empty-body 200 response', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 200 })));
        await expect(apiClient.post('/api/quest/reject', {})).resolves.toBeUndefined();
    });

    it('surfaces the backend detail string on an HTTP error', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: '本日は完了済みです' }, 400)));
        await expect(apiClient.post('/api/quest/complete', {})).rejects.toThrow('本日は完了済みです');
    });

    it('joins FastAPI 422 validation detail array messages instead of "API Error: 422"', async () => {
        const detail = [
            { loc: ['body', 'quest_id'], msg: 'field required', type: 'value_error.missing' },
            { loc: ['body', 'user_id'], msg: 'none is not an allowed value', type: 'type_error.none.not_allowed' },
        ];
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail }, 422)));
        await expect(apiClient.post('/api/quest/complete', {}))
            .rejects.toThrow('field required / none is not an allowed value');
    });

    it('falls back to "API Error: <status>" when detail is missing or unrecognized', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({}, 500)));
        await expect(apiClient.post('/api/quest/complete', {})).rejects.toThrow('API Error: 500');
    });

    it('converts a fetch-level TypeError (offline/DNS/CORS) into a friendly Japanese message', async () => {
        vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
        await expect(apiClient.get('/api/quest/data'))
            .rejects.toThrow('通信エラーが発生しました。ネットワーク接続をご確認のうえ、再度お試しください。');
    });
});
