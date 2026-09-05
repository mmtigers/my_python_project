import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useGameData } from './useGameData';
import { apiClient } from '../lib/apiClient';

// #473: viewerUserIdRefはqueryKeyに含まれないため、以前はユーザー切替直後の
// 1回のフェッチにcurrentUserIdxの変更が反映されず、次のポーリング(最大10秒後)
// まで新しいviewer_user_idが送られなかった。切替を検知した時点で即座に
// 再フェッチされることを確認する。

vi.mock('../lib/apiClient', () => ({
    apiClient: {
        get: vi.fn(),
        post: vi.fn(),
    },
}));

function makeGameData() {
    return {
        users: [
            { user_id: 'dad', name: 'Dad', level: 1, exp: 0, gold: 100 },
            { user_id: 'son', name: 'Son', level: 1, exp: 0, gold: 100 },
        ],
        quests: [],
        rewards: [],
        completedQuests: [],
        pendingQuests: [],
    };
}

function createWrapper() {
    const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false } },
    });
    return function Wrapper({ children }: { children: React.ReactNode }) {
        return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
    };
}

describe('useGameData viewer switch triggers an immediate refetch (#473)', () => {
    afterEach(() => {
        vi.restoreAllMocks();
    });

    it('refetches immediately with the new viewer_user_id when currentUserIdx changes', async () => {
        const getMock = vi.mocked(apiClient.get);
        getMock.mockResolvedValue(makeGameData());

        const wrapper = createWrapper();
        const { result, rerender } = renderHook(
            ({ idx }: { idx: number }) => useGameData(idx),
            { wrapper, initialProps: { idx: 0 } }
        );

        await waitFor(() => expect(result.current.users[0]?.user_id).toBe('dad'));

        const callCountBeforeSwitch = getMock.mock.calls.length;

        rerender({ idx: 1 });

        await waitFor(() => expect(getMock.mock.calls.length).toBeGreaterThan(callCountBeforeSwitch));

        const lastCallUrl = getMock.mock.calls[getMock.mock.calls.length - 1][0] as string;
        expect(lastCallUrl).toContain('viewer_user_id=son');
    });

    it('does not refetch again on re-render when currentUserIdx stays the same', async () => {
        const getMock = vi.mocked(apiClient.get);
        getMock.mockResolvedValue(makeGameData());

        const wrapper = createWrapper();
        const { result, rerender } = renderHook(
            ({ idx }: { idx: number }) => useGameData(idx),
            { wrapper, initialProps: { idx: 0 } }
        );

        await waitFor(() => expect(result.current.users[0]?.user_id).toBe('dad'));
        const callCountAfterInitialLoad = getMock.mock.calls.length;

        rerender({ idx: 0 });

        // 変化が無い以上、余分な即時再フェッチは発生しないこと
        await Promise.resolve();
        expect(getMock.mock.calls.length).toBe(callCountAfterInitialLoad);
    });
});

describe('useGameData buyReward validates the purchase response (#444)', () => {
    afterEach(() => {
        vi.restoreAllMocks();
    });

    it('succeeds and returns newGold for a valid purchase response', async () => {
        const getMock = vi.mocked(apiClient.get);
        getMock.mockResolvedValue(makeGameData());
        const postMock = vi.mocked(apiClient.post);
        postMock.mockResolvedValue({ status: 'purchased', newGold: 40 });

        const wrapper = createWrapper();
        const { result } = renderHook(() => useGameData(0), { wrapper });

        await waitFor(() => expect(result.current.users[0]?.user_id).toBe('dad'));

        const user = result.current.users[0];
        const reward = { reward_id: 1, title: 'おやつ', cost_gold: 10 } as never;

        const outcome = await result.current.buyReward(user, reward);

        expect(outcome).toMatchObject({ success: true, newGold: 40 });
    });

    it('surfaces a validation failure instead of silently passing through a malformed response', async () => {
        const getMock = vi.mocked(apiClient.get);
        getMock.mockResolvedValue(makeGameData());
        const postMock = vi.mocked(apiClient.post);
        // #444の回帰確認: newGoldが欠落した不正なレスポンスは、以前は無検証キャストで
        // そのまま通過し、res.newGoldがundefinedになる幽霊フィールドバグになっていた。
        postMock.mockResolvedValue({ status: 'purchased' });

        const wrapper = createWrapper();
        const { result } = renderHook(() => useGameData(0), { wrapper });

        await waitFor(() => expect(result.current.users[0]?.user_id).toBe('dad'));

        const user = result.current.users[0];
        const reward = { reward_id: 1, title: 'おやつ', cost_gold: 10 } as never;

        const outcome = await result.current.buyReward(user, reward);

        expect(outcome.success).toBe(false);
        expect((outcome as { reason?: string }).reason).toBe('error');
    });
});
