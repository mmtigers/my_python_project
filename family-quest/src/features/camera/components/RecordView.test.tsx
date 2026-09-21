import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import RecordView from './RecordView';
import { CameraConfig } from '../types';

// HlsPlayer は onVideoRef を useEffect の依存配列に含めており、関数が変わるたびに
// HLS.js のセットアップをやり直す(components/ui/HlsPlayer.tsx)。RecordView は親の
// 再描画でカメラ配列が作り直されても、カメラごとに**同じ関数**を渡し続けなければならない。
//
// eslint-plugin-react-hooks 7 の refs ルールに合わせて、ref への遅延書き込みから
// ID の並びをキーにしたメモ化へ書き換えたため、その約束をここで固定する。

const receivedRefSetters: Record<string, Array<(el: HTMLVideoElement | null) => void>> = {};

vi.mock('../../../components/ui/HlsPlayer', () => ({
    default: (props: { streamUrl: string; onVideoRef?: (el: HTMLVideoElement | null) => void }) => {
        const cameraId = props.streamUrl.split('/')[4];
        if (props.onVideoRef) {
            (receivedRefSetters[cameraId] ??= []).push(props.onVideoRef);
        }
        return <div data-testid={`player-${cameraId}`} />;
    },
}));

vi.mock('@/lib/apiClient', () => ({
    apiClient: { get: vi.fn().mockResolvedValue({ offset_seconds: 0 }) },
}));

const makeCameras = (): CameraConfig[] => [
    { id: 'entrance', name: '玄関', order: 1, enabled: true },
    { id: 'garden', name: '庭', order: 2, enabled: true },
];

async function startPlayback() {
    fireEvent.change(document.querySelector('input[type="date"]') as HTMLInputElement, { target: { value: '2026-09-21' } });
    fireEvent.change(document.querySelector('input[type="time"]') as HTMLInputElement, { target: { value: '09:30' } });
    fireEvent.click(screen.getByText('再生開始'));
    await waitFor(() => expect(screen.getByTestId('player-entrance')).toBeInTheDocument());
}

describe('RecordView keeps a stable onVideoRef per camera', () => {
    afterEach(() => {
        cleanup();
        for (const k of Object.keys(receivedRefSetters)) delete receivedRefSetters[k];
    });

    it('passes the same function across re-renders even when the cameras array is recreated', async () => {
        const { rerender } = render(<RecordView cameras={makeCameras()} />);
        await startPlayback();

        // 親の再描画を模す: 中身は同じだが別の配列オブジェクト
        rerender(<RecordView cameras={makeCameras()} />);
        rerender(<RecordView cameras={makeCameras()} />);

        for (const cameraId of ['entrance', 'garden']) {
            const setters = receivedRefSetters[cameraId];
            expect(setters.length).toBeGreaterThanOrEqual(2);
            expect(new Set(setters).size, `${cameraId} の onVideoRef が再描画で変わった`).toBe(1);
        }
    });

    it('gives each camera its own function', async () => {
        render(<RecordView cameras={makeCameras()} />);
        await startPlayback();
        expect(receivedRefSetters.entrance[0]).not.toBe(receivedRefSetters.garden[0]);
    });
});
