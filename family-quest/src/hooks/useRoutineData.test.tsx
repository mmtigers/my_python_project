import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useRoutineData } from './useRoutineData';
import { apiClient } from '../lib/apiClient';

// コードレビューで発覚: チェックポイント通過ボーナスのレベルアップは、本人が
// ステップを完了した「その場」だけでなく、何も操作せず自由時間中に締切時刻を
// 過ぎた場合はポーリング(GET /today)側で受動的に起こる
// (services/routine_service.py._apply_forced_transitionがGET経路からも呼ばれる、
// MY_HOME_SYSTEM/tests/test_routine_service.py::test_checkpoint_bonus_level_up_is_reported_in_response
// が検証する経路と同じ)。以前はcompleteStepMutationのonSuccessでしかonLevelUpを
// 呼んでおらず、この経路のレベルアップはサーバー側で起きているのにトーストが
// 一切出なかった。

vi.mock('../lib/apiClient', () => ({
    apiClient: {
        get: vi.fn(),
        post: vi.fn(),
    },
}));

function makeFlow(overrides: Record<string, unknown> = {}) {
    return {
        started: true,
        title: '起きてから出発まで',
        checkpoint_time: '07:50',
        current_step_index: 5,
        in_free_time: false,
        is_complete: false,
        bonus_gold: 150,
        bonus_exp: 30,
        leveled_up: false,
        new_level: null,
        steps: [],
        ...overrides,
    };
}

function makeTodayResponse(amOverrides: Record<string, unknown> = {}) {
    return {
        date: '2024-01-01',
        flows: {
            am: makeFlow(amOverrides),
            pm: { started: false, title: '帰ってから寝るまで' },
        },
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

describe('useRoutineData announces level-ups discovered by polling (code review finding)', () => {
    afterEach(() => {
        vi.restoreAllMocks();
    });

    it('calls onLevelUp when a GET /today poll reports a passively-granted level-up', async () => {
        const getMock = vi.mocked(apiClient.get);
        getMock.mockResolvedValue(makeTodayResponse({ leveled_up: true, new_level: 2 }));

        const onLevelUp = vi.fn();
        const wrapper = createWrapper();
        renderHook(() => useRoutineData('daughter', onLevelUp), { wrapper });

        await waitFor(() => expect(onLevelUp).toHaveBeenCalledWith({ newLevel: 2 }));
        expect(onLevelUp).toHaveBeenCalledTimes(1);
    });

    it('does not call onLevelUp when no flow reports a level-up', async () => {
        const getMock = vi.mocked(apiClient.get);
        getMock.mockResolvedValue(makeTodayResponse());

        const onLevelUp = vi.fn();
        const wrapper = createWrapper();
        const { result } = renderHook(() => useRoutineData('daughter', onLevelUp), { wrapper });

        await waitFor(() => expect(result.current.flows?.am).toBeDefined());
        expect(onLevelUp).not.toHaveBeenCalled();
    });

    it('does not announce the same level-up event twice on a re-render with unchanged data', async () => {
        const getMock = vi.mocked(apiClient.get);
        getMock.mockResolvedValue(makeTodayResponse({ leveled_up: true, new_level: 2 }));

        const onLevelUp = vi.fn();
        const wrapper = createWrapper();
        const { rerender } = renderHook(() => useRoutineData('daughter', onLevelUp), { wrapper });

        await waitFor(() => expect(onLevelUp).toHaveBeenCalledTimes(1));

        rerender();
        await Promise.resolve();

        expect(onLevelUp).toHaveBeenCalledTimes(1);
    });
});
