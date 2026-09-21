import { renderHook, act } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useLayoutMode } from './useLayoutMode';

// matchMedia は jsdom に実装が無いので差し替える。
// modern(addEventListener)と legacy(addListener・Safari 13以前)の両方を再現し、
// どちらの経路でも購読・解除されることを確認する。
function installMatchMedia({ matches, legacy = false }: { matches: boolean; legacy?: boolean }) {
    const listeners = new Set<() => void>();
    const mql = {
        matches,
        addEventListener: legacy ? undefined : vi.fn((_: string, cb: () => void) => listeners.add(cb)),
        removeEventListener: legacy ? undefined : vi.fn((_: string, cb: () => void) => listeners.delete(cb)),
        addListener: legacy ? vi.fn((cb: () => void) => listeners.add(cb)) : undefined,
        removeListener: legacy ? vi.fn((cb: () => void) => listeners.delete(cb)) : undefined,
    };
    vi.stubGlobal('matchMedia', vi.fn(() => mql));
    return {
        mql,
        emit(next: boolean) {
            mql.matches = next;
            listeners.forEach(cb => cb());
        },
    };
}

afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

describe('useLayoutMode', () => {
    it('横長のメディアクエリに一致すれば landscape', () => {
        installMatchMedia({ matches: true });
        expect(renderHook(() => useLayoutMode()).result.current).toBe('landscape');
    });

    it('一致しなければ portrait', () => {
        installMatchMedia({ matches: false });
        expect(renderHook(() => useLayoutMode()).result.current).toBe('portrait');
    });

    it('回転・リサイズに追従する', () => {
        const media = installMatchMedia({ matches: false });
        const { result } = renderHook(() => useLayoutMode());
        expect(result.current).toBe('portrait');

        act(() => media.emit(true));
        expect(result.current).toBe('landscape');

        act(() => media.emit(false));
        expect(result.current).toBe('portrait');
    });

    it('アンマウントで購読を解除する', () => {
        const media = installMatchMedia({ matches: false });
        const { unmount } = renderHook(() => useLayoutMode());
        unmount();
        expect(media.mql.removeEventListener).toHaveBeenCalled();
    });

    // Safari 13以前は addEventListener 非対応で、addListener へのフォールバックが要る。
    // ここが壊れると古い端末だけレイアウトが回転に追従しなくなり、CI では気づけない。
    it('addEventListener が無い環境では addListener へフォールバックする', () => {
        const media = installMatchMedia({ matches: false, legacy: true });
        const { result, unmount } = renderHook(() => useLayoutMode());

        expect(media.mql.addListener).toHaveBeenCalled();
        act(() => media.emit(true));
        expect(result.current).toBe('landscape');

        unmount();
        expect(media.mql.removeListener).toHaveBeenCalled();
    });

    it('matchMedia が無い環境では portrait に倒す', () => {
        vi.stubGlobal('matchMedia', undefined);
        expect(renderHook(() => useLayoutMode()).result.current).toBe('portrait');
    });
});
