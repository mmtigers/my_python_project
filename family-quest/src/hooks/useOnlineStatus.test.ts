import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useOnlineStatus } from './useOnlineStatus';

describe('useOnlineStatus', () => {
    const originalOnLine = window.navigator.onLine;

    beforeEach(() => {
        Object.defineProperty(window.navigator, 'onLine', { value: true, configurable: true, writable: true });
    });

    afterEach(() => {
        Object.defineProperty(window.navigator, 'onLine', { value: originalOnLine, configurable: true, writable: true });
    });

    it('reflects navigator.onLine on mount', () => {
        Object.defineProperty(window.navigator, 'onLine', { value: false, configurable: true, writable: true });
        const { result } = renderHook(() => useOnlineStatus());
        expect(result.current).toBe(false);
    });

    it('flips to false on a window "offline" event', () => {
        const { result } = renderHook(() => useOnlineStatus());
        expect(result.current).toBe(true);

        act(() => {
            window.dispatchEvent(new Event('offline'));
        });
        expect(result.current).toBe(false);
    });

    it('flips back to true on a window "online" event', () => {
        const { result } = renderHook(() => useOnlineStatus());

        act(() => {
            window.dispatchEvent(new Event('offline'));
        });
        expect(result.current).toBe(false);

        act(() => {
            window.dispatchEvent(new Event('online'));
        });
        expect(result.current).toBe(true);
    });
});
