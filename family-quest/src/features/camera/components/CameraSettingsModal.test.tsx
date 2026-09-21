import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import CameraSettingsModal from './CameraSettingsModal';
import { CameraConfig } from '../types';
import { apiClient } from '@/lib/apiClient';

vi.mock('@/lib/apiClient', () => ({
    apiClient: { put: vi.fn() },
}));

const putMock = vi.mocked(apiClient.put);

const cameras: CameraConfig[] = [
    { id: 'entrance', name: '玄関', order: 1, enabled: true },
    { id: 'garden', name: '庭', order: 2, enabled: false },
];

function renderModal(overrides: Partial<React.ComponentProps<typeof CameraSettingsModal>> = {}) {
    const onToggled = vi.fn().mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(
        <CameraSettingsModal
            isOpen
            onClose={onClose}
            cameras={cameras}
            onToggled={onToggled}
            {...overrides}
        />,
    );
    return { onToggled, onClose };
}

beforeEach(() => {
    putMock.mockReset();
    putMock.mockResolvedValue({});
    vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
});

describe('CameraSettingsModal', () => {
    it('閉じているときは何も描画しない', () => {
        renderModal({ isOpen: false });
        expect(screen.queryByText('カメラ設定')).not.toBeInTheDocument();
    });

    it('各カメラの現在の enabled をチェックボックスに反映する', () => {
        renderModal();
        const [entrance, garden] = screen.getAllByRole('checkbox') as HTMLInputElement[];
        expect(entrance.checked).toBe(true);
        expect(garden.checked).toBe(false);
    });

    it('切り替えると enabled を反転した値を PUT し、親に再取得を促す', async () => {
        const { onToggled } = renderModal();
        fireEvent.click(screen.getAllByRole('checkbox')[0]);

        await waitFor(() => expect(onToggled).toHaveBeenCalledTimes(1));
        expect(putMock).toHaveBeenCalledWith('/api/cameras/settings/entrance', { enabled: false });
    });

    it('無効なカメラをオンにするときも同じ経路を通る', async () => {
        const { onToggled } = renderModal();
        fireEvent.click(screen.getAllByRole('checkbox')[1]);

        await waitFor(() => expect(onToggled).toHaveBeenCalledTimes(1));
        expect(putMock).toHaveBeenCalledWith('/api/cameras/settings/garden', { enabled: true });
    });

    it('PUT が失敗したらエラーを表示し、再取得は呼ばない', async () => {
        putMock.mockRejectedValue(new Error('boom'));
        const { onToggled } = renderModal();
        fireEvent.click(screen.getAllByRole('checkbox')[0]);

        expect(await screen.findByText('設定の更新に失敗しました。')).toBeInTheDocument();
        expect(onToggled).not.toHaveBeenCalled();
    });

    // 失敗時に pendingId を finally で戻していないと、そのカメラのチェックボックスが
    // disabled のまま操作不能になる(モーダルを開き直すまで直らない)。
    it('失敗してもチェックボックスは操作可能なまま戻る', async () => {
        putMock.mockRejectedValue(new Error('boom'));
        renderModal();
        const checkbox = screen.getAllByRole('checkbox')[0] as HTMLInputElement;
        fireEvent.click(checkbox);

        await screen.findByText('設定の更新に失敗しました。');
        await waitFor(() => expect(checkbox.disabled).toBe(false));
    });

    it('失敗の次に成功したらエラー表示が消える', async () => {
        putMock.mockRejectedValueOnce(new Error('boom'));
        renderModal();
        fireEvent.click(screen.getAllByRole('checkbox')[0]);
        await screen.findByText('設定の更新に失敗しました。');

        fireEvent.click(screen.getAllByRole('checkbox')[0]);
        await waitFor(() => expect(screen.queryByText('設定の更新に失敗しました。')).not.toBeInTheDocument());
    });

    it('カメラが1台も無いときは空状態を出す', () => {
        renderModal({ cameras: [] });
        expect(screen.getByText('登録されているカメラがありません。')).toBeInTheDocument();
        expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
    });
});
