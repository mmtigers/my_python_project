import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import TvBlockHeaderBanner from './TvBlockHeaderBanner';
import { apiClient } from '../../lib/apiClient';

vi.mock('../../lib/apiClient', () => ({
    apiClient: { fetchTvBlock: vi.fn() },
}));

function renderBanner() {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={queryClient}>
            <TvBlockHeaderBanner />
        </QueryClientProvider>,
    );
}

describe('TvBlockHeaderBanner', () => {
    afterEach(() => {
        cleanup();
        vi.clearAllMocks();
    });

    it('休日はユーザーを選ばず、画面上部にテレビおやすみを出す', async () => {
        vi.mocked(apiClient.fetchTvBlock).mockResolvedValue({
            is_blocked: false,
            blocked_until: null,
            next_block_starts_at: '12:00',
            seconds_until_next_block: 5 * 3600,
            windows: [{ start: '12:00', end: '14:00' }],
        });
        renderBanner();
        expect(await screen.findByText('12:00〜14:00')).toBeInTheDocument();
    });

    it('平日(null)は何も出さない', async () => {
        vi.mocked(apiClient.fetchTvBlock).mockResolvedValue(null);
        const { container } = renderBanner();
        await vi.waitFor(() => expect(apiClient.fetchTvBlock).toHaveBeenCalled());
        expect(container).toBeEmptyDOMElement();
    });

    it('取得に失敗しても何も出さない(他の画面を壊さない)', async () => {
        vi.mocked(apiClient.fetchTvBlock).mockRejectedValue(new Error('boom'));
        const { container } = renderBanner();
        await vi.waitFor(() => expect(apiClient.fetchTvBlock).toHaveBeenCalled());
        expect(container).toBeEmptyDOMElement();
    });
});
