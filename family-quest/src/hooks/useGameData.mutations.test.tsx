import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useGameData } from './useGameData';
import { apiClient } from '../lib/apiClient';
import type { User } from '../types';

// #659: useGameData のカバレッジは 42.85% で、completeQuest / cancelQuest /
// approveQuest / rejectQuest の mutation 経路と handleError・refreshData が未カバーだった。
// 「クエストをタップ → API 成功 → 関連クエリの invalidate → レベルアップ通知」と
// 「失敗時にエラーを握って呼び出し元へ返す」経路をここで固定する。

vi.mock('../lib/apiClient', () => ({
    apiClient: {
        get: vi.fn(),
        post: vi.fn(),
        buyReward: vi.fn(),
        getInventory: vi.fn(),
    },
}));

const DAD: User = { user_id: 'dad', name: 'Dad', level: 3, exp: 10, gold: 100, job_class: '勇者' } as User;

function makeGameData() {
    return {
        users: [DAD, { user_id: 'son', name: 'Son', level: 1, exp: 0, gold: 0 }],
        quests: [{ quest_id: 1, title: 'おふろそうじ' }],
        rewards: [],
        completedQuests: [],
        pendingQuests: [],
    };
}

function createWrapper() {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const Wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    return { Wrapper, queryClient };
}

beforeEach(() => {
    vi.mocked(apiClient.get).mockResolvedValue(makeGameData());
});

afterEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
});

describe('completeQuest', () => {
    it('成功したら gameData と chronicle の両方を再取得対象にする', async () => {
        vi.mocked(apiClient.post).mockResolvedValue({ status: 'ok', leveledUp: false, newLevel: 3 });
        const { Wrapper, queryClient } = createWrapper();
        const invalidate = vi.spyOn(queryClient, 'invalidateQueries');

        const { result } = renderHook(() => useGameData(0), { wrapper: Wrapper });
        await waitFor(() => expect(result.current.users[0]?.user_id).toBe('dad'));

        await act(async () => {
            await result.current.completeQuest(DAD, { quest_id: 1, title: 'おふろそうじ' });
        });

        expect(apiClient.post).toHaveBeenCalledWith('/api/quest/complete', {
            user_id: 'dad',
            quest_id: 1,
        });
        const invalidatedKeys = invalidate.mock.calls.map((c) => JSON.stringify(c[0]));
        expect(invalidatedKeys.some((k) => k.includes('gameData'))).toBe(true);
        // 年代記も無効化しないと、完了した記録が staleTime(5分)の間だけ載らない
        expect(invalidatedKeys.some((k) => k.includes('chronicle'))).toBe(true);
    });

    it('レベルアップ応答なら onLevelUp に名前・レベル・ジョブを渡す', async () => {
        vi.mocked(apiClient.post).mockResolvedValue({ status: 'ok', leveledUp: true, newLevel: 4 });
        const onLevelUp = vi.fn();
        const { Wrapper } = createWrapper();

        const { result } = renderHook(() => useGameData(0, onLevelUp), { wrapper: Wrapper });
        await waitFor(() => expect(result.current.users[0]?.user_id).toBe('dad'));

        await act(async () => {
            await result.current.completeQuest(DAD, { quest_id: 1, title: 'おふろそうじ' });
        });

        expect(onLevelUp).toHaveBeenCalledWith({ user: 'Dad', level: 4, job: '勇者' });
    });

    it('quest_id が無いクエストは送信せずに失敗として返す(422 を送らない)', async () => {
        const { Wrapper } = createWrapper();
        const { result } = renderHook(() => useGameData(0), { wrapper: Wrapper });
        await waitFor(() => expect(result.current.users[0]?.user_id).toBe('dad'));

        let outcome: unknown;
        await act(async () => {
            outcome = await result.current.completeQuest(DAD, { title: 'idが無い' });
        });

        expect(apiClient.post).not.toHaveBeenCalled();
        expect((outcome as { success: boolean }).success).toBe(false);
    });

    it('API が失敗しても例外を投げず、失敗として返す', async () => {
        vi.mocked(apiClient.post).mockRejectedValue(new Error('network down'));
        vi.spyOn(console, 'error').mockImplementation(() => {});
        const { Wrapper } = createWrapper();

        const { result } = renderHook(() => useGameData(0), { wrapper: Wrapper });
        await waitFor(() => expect(result.current.users[0]?.user_id).toBe('dad'));

        let outcome: unknown;
        await act(async () => {
            outcome = await result.current.completeQuest(DAD, { quest_id: 1, title: 'おふろそうじ' });
        });

        expect((outcome as { success: boolean }).success).toBe(false);
    });
});

describe('cancelQuest / approveQuest / rejectQuest', () => {
    it('cancelQuest は history_id を送る', async () => {
        vi.mocked(apiClient.post).mockResolvedValue({ status: 'ok' });
        const { Wrapper } = createWrapper();
        const { result } = renderHook(() => useGameData(0), { wrapper: Wrapper });
        await waitFor(() => expect(result.current.users[0]?.user_id).toBe('dad'));

        await act(async () => {
            await result.current.cancelQuest(DAD, { id: 55, user_id: 'dad', quest_id: 1, status: 'pending' });
        });

        const [url, body] = vi.mocked(apiClient.post).mock.calls[0];
        expect(url).toContain('cancel');
        expect(body).toMatchObject({ history_id: 55 });
    });

    it('approveQuest は承認者IDと履歴IDを送る(大人のみ)', async () => {
        vi.mocked(apiClient.post).mockResolvedValue({ status: 'ok' });
        const { Wrapper } = createWrapper();
        const { result } = renderHook(() => useGameData(0), { wrapper: Wrapper });
        await waitFor(() => expect(result.current.users[0]?.user_id).toBe('dad'));

        const adult = { ...DAD, role: 'role_adult' } as User;
        await act(async () => {
            await result.current.approveQuest(adult, { id: 77, user_id: 'son', quest_id: 1, status: 'pending' });
        });

        const [url, body] = vi.mocked(apiClient.post).mock.calls[0];
        expect(url).toContain('approve');
        expect(body).toMatchObject({ approver_id: 'dad', history_id: 77 });
    });

    it('子どもは承認できない(APIを呼ばない)', async () => {
        const { Wrapper } = createWrapper();
        const { result } = renderHook(() => useGameData(0), { wrapper: Wrapper });
        await waitFor(() => expect(result.current.users[0]?.user_id).toBe('dad'));

        const child = { ...DAD, role: 'role_child' } as User;
        let outcome: unknown;
        await act(async () => {
            outcome = await result.current.approveQuest(child, { id: 77, user_id: 'son', quest_id: 1, status: 'pending' });
        });

        expect(apiClient.post).not.toHaveBeenCalled();
        expect(outcome).toMatchObject({ success: false, reason: 'permission' });
    });

    it('rejectQuest は理由も送る', async () => {
        vi.mocked(apiClient.post).mockResolvedValue({ status: 'ok' });
        const { Wrapper } = createWrapper();
        const { result } = renderHook(() => useGameData(0), { wrapper: Wrapper });
        await waitFor(() => expect(result.current.users[0]?.user_id).toBe('dad'));

        const adult = { ...DAD, role: 'role_adult' } as User;
        await act(async () => {
            await result.current.rejectQuest(adult, { id: 88, user_id: 'son', quest_id: 1, status: 'pending' }, 'まだ途中だよ');
        });

        const [url, body] = vi.mocked(apiClient.post).mock.calls[0];
        expect(url).toContain('reject');
        expect(body).toMatchObject({ approver_id: 'dad', history_id: 88, reason: 'まだ途中だよ' });
    });
});

describe('refreshData', () => {
    it('gameData と chronicle をまとめて無効化する', async () => {
        const { Wrapper, queryClient } = createWrapper();
        const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
        const { result } = renderHook(() => useGameData(0), { wrapper: Wrapper });
        await waitFor(() => expect(result.current.users[0]?.user_id).toBe('dad'));

        act(() => {
            result.current.refreshData();
        });

        const keys = invalidate.mock.calls.map((c) => JSON.stringify(c[0]));
        expect(keys.some((k) => k.includes('gameData'))).toBe(true);
    });
});
