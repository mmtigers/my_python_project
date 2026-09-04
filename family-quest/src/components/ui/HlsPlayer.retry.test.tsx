import { render, screen, cleanup, fireEvent, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// #392: hls.js の NETWORK_ERROR(fatal) を指数バックオフで startLoad() 再試行し、
// 上限超過で「再試行」ボタン付きエラー表示に落ちること、ボタンで HLS を再生成すること、
// アンマウント時に onVideoRef(null) を呼ぶこと(F-L4)を、hls.js をモックして検証する。

type ErrorHandler = (event: string, data: { fatal: boolean; type: string }) => void;

const mockState = vi.hoisted(() => ({
    instances: [] as Array<{
        handlers: Record<string, (...args: unknown[]) => void>;
        startLoad: ReturnType<typeof vi.fn>;
        destroy: ReturnType<typeof vi.fn>;
    }>,
}));

vi.mock('hls.js', () => {
    class FakeHls {
        static Events = { ERROR: 'hlsError', MANIFEST_PARSED: 'hlsManifestParsed', FRAG_LOADED: 'hlsFragLoaded' };
        static ErrorTypes = { NETWORK_ERROR: 'networkError', MEDIA_ERROR: 'mediaError', OTHER_ERROR: 'otherError' };
        static isSupported = () => true;
        handlers: Record<string, (...args: unknown[]) => void> = {};
        startLoad = vi.fn();
        destroy = vi.fn();
        recoverMediaError = vi.fn();
        loadSource = vi.fn();
        attachMedia = vi.fn();
        constructor() {
            mockState.instances.push(this);
        }
        on(event: string, handler: (...args: unknown[]) => void) {
            this.handlers[event] = handler;
        }
    }
    return { default: FakeHls };
});

import HlsPlayer from './HlsPlayer';

const latest = () => mockState.instances[mockState.instances.length - 1];
const fireFatal = (type: string) => {
    act(() => {
        (latest().handlers['hlsError'] as ErrorHandler)('hlsError', { fatal: true, type });
    });
};

describe('HlsPlayer network error retry (#392)', () => {
    beforeEach(() => {
        mockState.instances.length = 0;
        vi.useFakeTimers();
        vi.spyOn(console, 'error').mockImplementation(() => {});
        vi.spyOn(console, 'warn').mockImplementation(() => {});
    });

    afterEach(() => {
        cleanup();
        vi.useRealTimers();
        vi.restoreAllMocks();
    });

    it('retries startLoad() with exponential backoff instead of destroying on NETWORK_ERROR', () => {
        render(<HlsPlayer streamUrl="/api/cameras/live/cam1/stream.m3u8" />);
        const hls = latest();

        fireFatal('networkError');
        expect(hls.destroy).not.toHaveBeenCalled();
        expect(hls.startLoad).not.toHaveBeenCalled();
        act(() => { vi.advanceTimersByTime(1000); });
        expect(hls.startLoad).toHaveBeenCalledTimes(1);

        // 2回目は 2000ms 後
        fireFatal('networkError');
        act(() => { vi.advanceTimersByTime(1999); });
        expect(hls.startLoad).toHaveBeenCalledTimes(1);
        act(() => { vi.advanceTimersByTime(1); });
        expect(hls.startLoad).toHaveBeenCalledTimes(2);
        expect(screen.queryByText('映像を取得できませんでした')).not.toBeInTheDocument();
    });

    it('resets the backoff once a fragment loads', () => {
        render(<HlsPlayer streamUrl="/api/cameras/live/cam1/stream.m3u8" />);
        const hls = latest();
        fireFatal('networkError');
        act(() => { vi.advanceTimersByTime(1000); });
        act(() => { hls.handlers['hlsFragLoaded'](); });

        // 回復後の次のエラーは再び 1000ms から
        fireFatal('networkError');
        act(() => { vi.advanceTimersByTime(1000); });
        expect(hls.startLoad).toHaveBeenCalledTimes(2);
    });

    it('gives up after the retry limit, shows a retry button, and recreates Hls when it is pressed', () => {
        render(<HlsPlayer streamUrl="/api/cameras/live/cam1/stream.m3u8" />);
        const hls = latest();

        for (let i = 0; i < 6; i++) {
            fireFatal('networkError');
            act(() => { vi.advanceTimersByTime(30 * 1000); });
        }
        expect(hls.startLoad).toHaveBeenCalledTimes(6);

        // 7回目で上限超過 → destroy + エラー表示
        fireFatal('networkError');
        expect(hls.destroy).toHaveBeenCalledTimes(1);
        expect(screen.getByText('映像を取得できませんでした')).toBeInTheDocument();

        expect(mockState.instances).toHaveLength(1);
        fireEvent.click(screen.getByRole('button', { name: '再試行' }));
        expect(mockState.instances).toHaveLength(2);
        expect(screen.queryByText('映像を取得できませんでした')).not.toBeInTheDocument();
    });

    it('fails fast on a non-network, non-media fatal error', () => {
        render(<HlsPlayer streamUrl="/api/cameras/live/cam1/stream.m3u8" />);
        const hls = latest();
        fireFatal('otherError');
        expect(hls.destroy).toHaveBeenCalledTimes(1);
        expect(hls.startLoad).not.toHaveBeenCalled();
        expect(screen.getByText('映像を取得できませんでした')).toBeInTheDocument();
    });

    it('calls onVideoRef(element) on mount and onVideoRef(null) on unmount (F-L4)', () => {
        const onVideoRef = vi.fn();
        const { unmount } = render(<HlsPlayer streamUrl="/api/cameras/live/cam1/stream.m3u8" onVideoRef={onVideoRef} />);
        expect(onVideoRef).toHaveBeenLastCalledWith(expect.any(HTMLVideoElement));
        unmount();
        expect(onVideoRef).toHaveBeenLastCalledWith(null);
        expect(latest().destroy).toHaveBeenCalled();
    });

    it('does not fire a pending retry after unmount', () => {
        const { unmount } = render(<HlsPlayer streamUrl="/api/cameras/live/cam1/stream.m3u8" />);
        const hls = latest();
        fireFatal('networkError');
        unmount();
        act(() => { vi.advanceTimersByTime(5000); });
        expect(hls.startLoad).not.toHaveBeenCalled();
    });
});
