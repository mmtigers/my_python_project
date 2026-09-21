import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import CameraDashboard from './CameraDashboard';
import { CameraConfig } from '../types';
import { apiClient } from '@/lib/apiClient';

vi.mock('@/lib/apiClient', () => ({
    apiClient: { get: vi.fn(), put: vi.fn() },
}));

// 子ビューは、受け取ったカメラの並びだけを検証できる形に差し替える。
vi.mock('./LiveView', () => ({
    default: ({ cameras }: { cameras: CameraConfig[] }) => (
        <div data-testid="live-view">{cameras.map(c => c.id).join(',')}</div>
    ),
}));
vi.mock('./RecordView', () => ({
    default: ({ cameras }: { cameras: CameraConfig[] }) => (
        <div data-testid="record-view">{cameras.map(c => c.id).join(',')}</div>
    ),
}));
vi.mock('./CameraSettingsModal', () => ({
    default: ({ isOpen, cameras }: { isOpen: boolean; cameras: CameraConfig[] }) =>
        isOpen ? <div data-testid="settings-modal">{cameras.map(c => c.id).join(',')}</div> : null,
}));

const getMock = vi.mocked(apiClient.get);

function renderDashboard() {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={queryClient}>
            <CameraDashboard />
        </QueryClientProvider>,
    );
}

beforeEach(() => {
    getMock.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    document.title = 'Family Quest';
});

describe('CameraDashboard', () => {
    it('order 順に並べ替え、無効なカメラをビューから除く', async () => {
        getMock.mockResolvedValue([
            { id: 'parking', name: '駐車場', order: 3, enabled: false },
            { id: 'garden', name: '庭', order: 2, enabled: true },
            { id: 'entrance', name: '玄関', order: 1, enabled: true },
        ]);
        renderDashboard();

        await waitFor(() => expect(screen.getByTestId('live-view')).toHaveTextContent('entrance,garden'));
        expect(getMock).toHaveBeenCalledWith('/api/cameras/settings');
    });

    // 設定モーダルは「無効にしたカメラを再び有効化する」ための唯一の導線なので、
    // ビュー側と違って**無効なカメラも含む全件**を渡す必要がある。
    // 片方のフィルタを両方に効かせてしまうと、一度オフにしたカメラを二度と
    // 戻せなくなる(UIからは復旧不能)。
    it('設定モーダルには無効なカメラも含めて渡す', async () => {
        getMock.mockResolvedValue([
            { id: 'entrance', name: '玄関', order: 1, enabled: true },
            { id: 'parking', name: '駐車場', order: 2, enabled: false },
        ]);
        renderDashboard();
        await screen.findByTestId('live-view');

        fireEvent.click(screen.getByLabelText('カメラ設定'));
        expect(screen.getByTestId('settings-modal')).toHaveTextContent('entrance,parking');
    });

    it('タブで録画再生に切り替わる', async () => {
        getMock.mockResolvedValue([{ id: 'entrance', name: '玄関', order: 1, enabled: true }]);
        renderDashboard();
        await screen.findByTestId('live-view');

        fireEvent.click(screen.getByText('📼 録画再生'));
        expect(screen.getByTestId('record-view')).toBeInTheDocument();
        expect(screen.queryByTestId('live-view')).not.toBeInTheDocument();
    });

    it('読み込み中は読み込み表示を出す', () => {
        getMock.mockReturnValue(new Promise(() => {}));
        renderDashboard();
        expect(screen.getByText('読み込み中...')).toBeInTheDocument();
    });

    // Issue #121: /camera は ToastProvider の外で独立してマウントされるため、
    // 失敗を画面内に出す以外に知らせる手段が無い。
    it('取得に失敗したらバナーに detail を出し、再試行で再取得する', async () => {
        getMock.mockRejectedValueOnce(new Error('カメラ設定ファイルが読めません'));
        renderDashboard();

        expect(await screen.findByText(/カメラ設定ファイルが読めません/)).toBeInTheDocument();

        getMock.mockResolvedValue([{ id: 'entrance', name: '玄関', order: 1, enabled: true }]);
        fireEvent.click(screen.getByText('再試行'));
        await waitFor(() => expect(screen.getByTestId('live-view')).toHaveTextContent('entrance'));
    });

    // #659: 取得境界で Zod 検証しているので、形が違えばそのデータは通らずエラーになる。
    // バナーと既存表示は共存する設計(キャッシュ済みデータは保持する)なので、
    // ビュー自体は残るが、壊れたレスポンスの中身は渡らないことを確認する。
    it('レスポンスの形が契約と違えばエラーとして扱い、壊れたデータを通さない', async () => {
        getMock.mockResolvedValue([{ id: 'entrance', name: '玄関', order: '1', enabled: true }]);
        renderDashboard();
        expect(await screen.findByText(/カメラ設定の取得に失敗しました/)).toBeInTheDocument();
        expect(screen.getByTestId('live-view')).toHaveTextContent('');
    });

    it('表示中はドキュメントタイトルを変え、離れたら戻す', async () => {
        getMock.mockResolvedValue([{ id: 'entrance', name: '玄関', order: 1, enabled: true }]);
        const { unmount } = renderDashboard();
        await waitFor(() => expect(document.title).toBe('ホーム監視カメラ'));
        unmount();
        expect(document.title).toBe('Family Quest');
    });
});
