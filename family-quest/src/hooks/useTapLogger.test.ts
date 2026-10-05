import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useTapLogger } from './useTapLogger';
import type { TapContext } from '../lib/tapLogger';

function tap(target: Element) {
    const e = new MouseEvent('pointerdown', { bubbles: true, clientX: 10, clientY: 10 });
    Object.defineProperty(e, 'isPrimary', { value: true });
    Object.defineProperty(e, 'pointerType', { value: 'touch' });
    target.dispatchEvent(e);
}

describe('useTapLogger', () => {
    let beacon: ReturnType<typeof vi.fn>;

    beforeEach(() => {
        window.localStorage.clear();
        beacon = vi.fn(() => true);
        Object.defineProperty(navigator, 'sendBeacon', { value: beacon, configurable: true, writable: true });
        document.body.innerHTML = '<button id="b">クエスト</button>';
    });

    afterEach(() => {
        document.body.innerHTML = '';
        window.localStorage.clear();
    });

    function hide() {
        Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true });
        document.dispatchEvent(new Event('visibilitychange'));
        Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
    }

    it('records taps with the latest context and sends them when the page is hidden', async () => {
        const first: TapContext = { userId: 'son', screen: 'quest', layoutMode: 'portrait' };
        const { rerender } = renderHook((ctx: TapContext) => useTapLogger(ctx), { initialProps: first });

        rerender({ userId: 'mom', screen: 'shop', layoutMode: 'portrait' });
        tap(document.querySelector('#b')!);
        hide();

        expect(beacon).toHaveBeenCalledTimes(1);
        const blob = beacon.mock.calls[0][1] as Blob;
        const body = JSON.parse(await blob.text());
        expect(body.events).toHaveLength(1);
        expect(body.events[0]).toMatchObject({ user_id: 'mom', screen: 'shop', element_id: 'クエスト' });
        expect(beacon.mock.calls[0][0]).toContain('/api/ui-log/events');
    });

    it('stops listening after unmount', () => {
        const { unmount } = renderHook(() => useTapLogger({ userId: 'son', screen: 'quest', layoutMode: null }));
        unmount();
        tap(document.querySelector('#b')!);
        hide();
        expect(beacon).not.toHaveBeenCalled();
    });
});
