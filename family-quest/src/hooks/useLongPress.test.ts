import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useLongPress } from './useLongPress';

// #389: 長押し発火後の猶予時間内は wasFiredRecently() が true を返し、
// 呼び出し側が「指を離した瞬間の click」を無視できることを検証する。

const pointerEvent = () => ({ stopPropagation: vi.fn() } as unknown as React.PointerEvent);

describe('useLongPress', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it('fires onLongPress after the threshold and reports wasFiredRecently within the suppress window', () => {
        const onLongPress = vi.fn();
        const { result } = renderHook(() => useLongPress({ onLongPress, thresholdMs: 550, clickSuppressMs: 400 }));

        expect(result.current.wasFiredRecently()).toBe(false);

        act(() => {
            result.current.handlers.onPointerDown(pointerEvent());
        });
        act(() => {
            vi.advanceTimersByTime(550);
        });
        expect(onLongPress).toHaveBeenCalledTimes(1);
        expect(result.current.wasFiredRecently()).toBe(true);

        // 指を離しても(pointerup)、猶予時間内は true のまま
        act(() => {
            result.current.handlers.onPointerUp(pointerEvent());
        });
        act(() => {
            vi.advanceTimersByTime(399);
        });
        expect(result.current.wasFiredRecently()).toBe(true);

        act(() => {
            vi.advanceTimersByTime(1);
        });
        expect(result.current.wasFiredRecently()).toBe(false);
    });

    it('does not report wasFiredRecently for a short tap that never reached the threshold', () => {
        const onLongPress = vi.fn();
        const onShortTap = vi.fn();
        const { result } = renderHook(() => useLongPress({ onLongPress, onShortTap, thresholdMs: 550 }));

        act(() => {
            result.current.handlers.onPointerDown(pointerEvent());
        });
        act(() => {
            vi.advanceTimersByTime(200);
        });
        act(() => {
            result.current.handlers.onPointerUp(pointerEvent());
        });

        expect(onLongPress).not.toHaveBeenCalled();
        expect(onShortTap).toHaveBeenCalledTimes(1);
        expect(result.current.wasFiredRecently()).toBe(false);
    });

    it('does nothing while disabled', () => {
        const onLongPress = vi.fn();
        const { result } = renderHook(() => useLongPress({ onLongPress, disabled: true }));

        act(() => {
            result.current.handlers.onPointerDown(pointerEvent());
        });
        act(() => {
            vi.advanceTimersByTime(1000);
        });
        expect(onLongPress).not.toHaveBeenCalled();
        expect(result.current.wasFiredRecently()).toBe(false);
    });
});
