import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Hls from 'hls.js';
import HlsPlayer from './HlsPlayer';

// #295: Safari(hls.js非対応・ネイティブHLS対応)パスでは video 要素に
// 'loadedmetadata'/'error' リスナーを直接 addEventListener しており、
// アンマウント時やstreamUrl変更時にきちんと removeEventListener されているかは
// これまでテストで担保されていなかった(CODE_REVIEW_REPORT_ALL.mの指摘)。
// hls.jsの再生自体はモックせず、addEventListener/removeEventListenerの呼び出し回数が
// 常に対になっていることだけを検証する。
describe('HlsPlayer (Safari native HLS path)', () => {
    afterEach(() => {
        cleanup();
        vi.restoreAllMocks();
    });

    const mockSafariNativeHls = () => {
        vi.spyOn(Hls, 'isSupported').mockReturnValue(false);
        vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockImplementation((type: string) =>
            type === 'application/vnd.apple.mpegurl' ? 'probably' : ''
        );
        vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    };

    // 注意: React-DOM自身も <video> のマウント時にloadedmetadata/error等の
    // メディアイベント用リスナーを内部的に(合成イベントシステムのため)登録する。
    // それと本コンポーネント自身の登録を区別するため、スパイは常に初回マウント
    // "完了後"に張る(初回マウント時点のReact-DOM分の登録はスパイに含めない)。

    it('removes the loadedmetadata/error listeners it added, on unmount', () => {
        mockSafariNativeHls();
        const { unmount } = render(<HlsPlayer streamUrl="/api/cameras/live/cam1/stream.m3u8" />);

        const removeSpy = vi.spyOn(HTMLVideoElement.prototype, 'removeEventListener');
        unmount();

        const removedTypes = removeSpy.mock.calls.map(([type]) => type).filter(t => t === 'loadedmetadata' || t === 'error');
        expect(removedTypes.sort()).toEqual(['error', 'loadedmetadata']);
    });

    it('removes the previous streamUrl listeners before attaching new ones on streamUrl change', () => {
        mockSafariNativeHls();
        const { rerender } = render(<HlsPlayer streamUrl="/api/cameras/live/cam1/stream.m3u8" />);

        const addSpy = vi.spyOn(HTMLVideoElement.prototype, 'addEventListener');
        const removeSpy = vi.spyOn(HTMLVideoElement.prototype, 'removeEventListener');

        rerender(<HlsPlayer streamUrl="/api/cameras/live/cam2/stream.m3u8" />);

        // 差し替え前のリスナーが1組だけ解除され、新しいリスナーが1組だけ追加されている
        // (積み上がっていない)ことを確認する
        const removedTypes = removeSpy.mock.calls.map(([t]) => t).filter(t => t === 'loadedmetadata' || t === 'error');
        const addedTypes = addSpy.mock.calls.map(([t]) => t).filter(t => t === 'loadedmetadata' || t === 'error');
        expect(removedTypes.sort()).toEqual(['error', 'loadedmetadata']);
        expect(addedTypes.sort()).toEqual(['error', 'loadedmetadata']);
    });
});

// #443: HlsPlayerがHLS非対応環境でサイレント失敗する(映像もエラー表示も出ない)問題の回帰テスト。
describe('HlsPlayer error visibility (#443)', () => {
    afterEach(() => {
        cleanup();
        vi.restoreAllMocks();
    });

    it('shows an error overlay when neither hls.js nor native HLS playback is available', () => {
        vi.spyOn(Hls, 'isSupported').mockReturnValue(false);
        vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockReturnValue('');

        render(<HlsPlayer streamUrl="/api/cameras/live/cam1/stream.m3u8" />);

        expect(screen.getByText('映像を取得できませんでした')).toBeInTheDocument();
    });

    it('shows an error overlay when video.play() fails on the Safari native path', async () => {
        vi.spyOn(Hls, 'isSupported').mockReturnValue(false);
        vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockImplementation((type: string) =>
            type === 'application/vnd.apple.mpegurl' ? 'probably' : ''
        );
        vi.spyOn(HTMLMediaElement.prototype, 'play').mockRejectedValue(new Error('NotAllowedError'));
        vi.spyOn(console, 'error').mockImplementation(() => {});

        const { container } = render(<HlsPlayer streamUrl="/api/cameras/live/cam1/stream.m3u8" />);
        const video = container.querySelector('video');
        expect(video).not.toBeNull();
        fireEvent(video as HTMLVideoElement, new Event('loadedmetadata'));

        await waitFor(() => expect(screen.getByText('映像を取得できませんでした')).toBeInTheDocument());
    });
});
