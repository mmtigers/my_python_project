import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { InventoryList } from './InventoryList';
import { apiClient } from '../../../lib/apiClient';
import { InventoryItem, InventoryResponse } from '../../../types';

// #441: InventoryListの一覧取得useQueryにエラーハンドリングが無く、取得失敗時に
// 画面上は何も表示されないサイレント失敗になっていた回帰テスト。
// 加えて、YouTubeごほうび券の視聴制限(クールダウン・1日の合計分数上限)の表示・
// 使用可否の出し分けもここで検証する。

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

function youtubeTicket(overrides: Partial<InventoryItem> = {}): InventoryItem {
    return {
        id: 1,
        reward_id: 11,
        title: 'Youtube (30:00)',
        icon: '📺',
        desc: '少し長めの動画も楽しめる',
        status: 'owned',
        purchased_at: '2026-09-21T10:00:00+09:00',
        is_youtube_reward: true,
        youtube_duration_minutes: 30,
        ...overrides,
    };
}

// サーバー(InventoryService.get_user_inventory)の応答の既定形。
// 既定は「制限は施行済み・今日はまだ使っていない」状態。
function inventoryResponse(overrides: Partial<InventoryResponse> = {}): InventoryResponse {
    return {
        items: [youtubeTicket()],
        youtube_cooldown_remaining_seconds: 0,
        youtube_cooldown_announcement: null,
        youtube_daily_limit_minutes: 60,
        youtube_daily_used_minutes: 0,
        youtube_daily_limit_announcement: null,
        youtube_extension: {
            minutes_per_quest: 30,
            granted_count: 0,
            max_per_day: 2,
            can_extend_now: false,
        },
        is_in_free_time: true,
        ...overrides,
    };
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
        vi.mocked(apiClient.fetchInventory).mockResolvedValue(inventoryResponse({ items: [] }));

        renderWithClient(<InventoryList userId="dad" />);

        await waitFor(() => expect(screen.getByText('まだなにも持っていません')).toBeInTheDocument());
        expect(showToastMock).not.toHaveBeenCalled();
    });
});

describe('InventoryList YouTube視聴制限', () => {
    afterEach(() => {
        cleanup();
        vi.resetAllMocks();
    });

    it('きょうの残り分数を表示する', async () => {
        vi.mocked(apiClient.fetchInventory).mockResolvedValue(
            inventoryResponse({ youtube_daily_used_minutes: 40 }),
        );

        renderWithClient(<InventoryList userId="son" />);

        await waitFor(() => expect(screen.getByText('あと20分')).toBeInTheDocument());
        expect(screen.getByText('(40/60分)')).toBeInTheDocument();
    });

    it('きょうの残り分数に収まらない券はタップできず、理由を表示する', async () => {
        // 残り20分に対して30分券 → 使えない
        vi.mocked(apiClient.fetchInventory).mockResolvedValue(
            inventoryResponse({ youtube_daily_used_minutes: 40 }),
        );

        renderWithClient(<InventoryList userId="son" />);

        const reason = await screen.findByText('きょうはあと20分。この券は使えません');
        expect(reason).toBeInTheDocument();

        // カードをタップしても「つかう」確認モーダルは開かない
        reason.click();
        expect(screen.queryByText('「Youtube (30:00)」を使いますか？')).not.toBeInTheDocument();
    });

    it('上限を使い切ったら「また明日」と伝える', async () => {
        vi.mocked(apiClient.fetchInventory).mockResolvedValue(
            inventoryResponse({ youtube_daily_used_minutes: 60 }),
        );

        renderWithClient(<InventoryList userId="son" />);

        await waitFor(() =>
            expect(screen.getByText('きょうのYouTubeはおしまい。また明日つかおうね')).toBeInTheDocument(),
        );
        expect(screen.getByText('きょうはおしまい')).toBeInTheDocument();
    });

    it('残り分数に収まる券は通常どおり使える', async () => {
        vi.mocked(apiClient.fetchInventory).mockResolvedValue(
            inventoryResponse({
                youtube_daily_used_minutes: 40,
                items: [youtubeTicket({ id: 2, reward_id: 10, title: 'Youtube (10:00)', youtube_duration_minutes: 10 })],
            }),
        );

        renderWithClient(<InventoryList userId="son" />);

        await waitFor(() => expect(screen.getByText('Youtube (10:00)')).toBeInTheDocument());
        // ロックされていないので、説明文がそのまま出ている
        expect(screen.getByText('少し長めの動画も楽しめる')).toBeInTheDocument();
    });

    it('日次上限が施行される前は、上限を超えていてもブロックしない(予告のみ)', async () => {
        vi.mocked(apiClient.fetchInventory).mockResolvedValue(
            inventoryResponse({
                youtube_daily_used_minutes: 60,
                youtube_daily_limit_announcement: { starts_on: '2026-09-27', days_remaining: 6 },
            }),
        );

        renderWithClient(<InventoryList userId="son" />);

        await waitFor(() =>
            expect(screen.getByText(/1日ぜんぶで60分までになります/)).toBeInTheDocument(),
        );
        // 施行前なので券はロックされず、説明文のまま
        expect(screen.getByText('少し長めの動画も楽しめる')).toBeInTheDocument();
        expect(screen.queryByText(/また明日つかおうね/)).not.toBeInTheDocument();
    });

    it('クールダウン中は残り時間を表示する', async () => {
        vi.mocked(apiClient.fetchInventory).mockResolvedValue(
            inventoryResponse({ youtube_cooldown_remaining_seconds: 13 * 60 + 5 }),
        );

        renderWithClient(<InventoryList userId="son" />);

        await waitFor(() =>
            expect(screen.getByText('目を休めよう。あと13:05で使えます')).toBeInTheDocument(),
        );
    });

    it('日次上限とクールダウンが同時なら、日次上限の理由を優先して表示する', async () => {
        vi.mocked(apiClient.fetchInventory).mockResolvedValue(
            inventoryResponse({
                youtube_daily_used_minutes: 60,
                youtube_cooldown_remaining_seconds: 13 * 60,
            }),
        );

        renderWithClient(<InventoryList userId="son" />);

        await waitFor(() =>
            expect(screen.getByText('きょうのYouTubeはおしまい。また明日つかおうね')).toBeInTheDocument(),
        );
        expect(screen.queryByText(/目を休めよう/)).not.toBeInTheDocument();
    });

    it('上限なし設定のときは残り分数を表示しない', async () => {
        vi.mocked(apiClient.fetchInventory).mockResolvedValue(
            inventoryResponse({ youtube_daily_limit_minutes: null }),
        );

        renderWithClient(<InventoryList userId="son" />);

        await waitFor(() => expect(screen.getByText('Youtube (30:00)')).toBeInTheDocument());
        expect(screen.queryByText(/きょうのYouTube:/)).not.toBeInTheDocument();
    });
});

describe('InventoryList プリントによる上限の延長', () => {
    afterEach(() => {
        cleanup();
        vi.resetAllMocks();
    });

    it('延長できる状態なら、残り分数の表示でプリントを案内する', async () => {
        vi.mocked(apiClient.fetchInventory).mockResolvedValue(
            inventoryResponse({
                youtube_daily_used_minutes: 60,
                youtube_extension: {
                    minutes_per_quest: 30,
                    granted_count: 0,
                    max_per_day: 2,
                    can_extend_now: true,
                },
            }),
        );

        renderWithClient(<InventoryList userId="son" />);

        await waitFor(() =>
            expect(screen.getByText('プリントを1枚やると、あと30分ふえるよ📝')).toBeInTheDocument(),
        );
    });

    it('延長できる状態なら、使えない券の理由もプリントの案内にする', async () => {
        vi.mocked(apiClient.fetchInventory).mockResolvedValue(
            inventoryResponse({
                youtube_daily_used_minutes: 60,
                youtube_extension: {
                    minutes_per_quest: 30,
                    granted_count: 0,
                    max_per_day: 2,
                    can_extend_now: true,
                },
            }),
        );

        renderWithClient(<InventoryList userId="son" />);

        await waitFor(() =>
            expect(
                screen.getByText('きょうのぶんはおしまい。プリントを1枚やると30分ふえるよ'),
            ).toBeInTheDocument(),
        );
        expect(screen.queryByText(/また明日つかおうね/)).not.toBeInTheDocument();
    });

    it('延長を使い切っていれば「また明日」に戻る', async () => {
        vi.mocked(apiClient.fetchInventory).mockResolvedValue(
            inventoryResponse({
                youtube_daily_limit_minutes: 120,
                youtube_daily_used_minutes: 120,
                youtube_extension: {
                    minutes_per_quest: 30,
                    granted_count: 2,
                    max_per_day: 2,
                    can_extend_now: false,
                },
            }),
        );

        renderWithClient(<InventoryList userId="son" />);

        await waitFor(() =>
            expect(screen.getByText('きょうのYouTubeはおしまい。また明日つかおうね')).toBeInTheDocument(),
        );
        expect(screen.queryByText(/プリントを1枚やると/)).not.toBeInTheDocument();
    });

    it('延長機能が無効(null)でも表示が壊れない', async () => {
        vi.mocked(apiClient.fetchInventory).mockResolvedValue(
            inventoryResponse({ youtube_daily_used_minutes: 60, youtube_extension: null }),
        );

        renderWithClient(<InventoryList userId="son" />);

        await waitFor(() =>
            expect(screen.getByText('きょうのYouTubeはおしまい。また明日つかおうね')).toBeInTheDocument(),
        );
        expect(screen.queryByText(/プリント/)).not.toBeInTheDocument();
    });
});
