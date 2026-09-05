import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { InventoryList } from './InventoryList';
import { apiClient } from '../../../lib/apiClient';

// #441: InventoryListの一覧取得useQueryにエラーハンドリングが無く、取得失敗時に
// 画面上は何も表示されないサイレント失敗になっていた回帰テスト。

vi.mock('../../../lib/apiClient', () => ({
    apiClient: {
        fetchInventory: vi.fn(),
        useItem: vi.fn(),
    },
}));

const showToastMock = vi.fn();
vi.mock('../../../context/useToast', () => ({
    useToast: () => ({ showToast: showToastMock }),
}));

function renderWithClient(ui: React.ReactElement) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe('InventoryList fetch error handling (#441)', () => {
    afterEach(() => {
        cleanup();
        vi.resetAllMocks();
    });

    it('shows an error toast when the inventory fetch fails', async () => {
        vi.mocked(apiClient.fetchInventory).mockRejectedValue(new Error('Network down'));

        renderWithClient(<InventoryList userId="dad" />);

        await waitFor(() => expect(showToastMock).toHaveBeenCalledTimes(1));
        expect(showToastMock).toHaveBeenCalledWith(expect.objectContaining({ title: 'エラー' }));
    });

    it('does not show an error toast when the fetch succeeds', async () => {
        vi.mocked(apiClient.fetchInventory).mockResolvedValue({
            items: [],
            youtube_cooldown_remaining_seconds: 0,
            youtube_cooldown_announcement: null,
        });

        renderWithClient(<InventoryList userId="dad" />);

        await waitFor(() => expect(screen.getByText('まだなにも持っていません')).toBeInTheDocument());
        expect(showToastMock).not.toHaveBeenCalled();
    });
});
