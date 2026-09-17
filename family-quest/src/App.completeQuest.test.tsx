/**
 * Issue #659 改善案1(a): クエスト完了フローの統合テスト。
 *
 * 「クエストをタップ → 確認モーダル → はい → completeQuest → トースト」と、
 * 失敗時の「MessageModal → 再試行」を、App.tsx の描画経由で固定する。
 *
 * 誤操作対策として完了に確認ダイアログを挟む設計(実機検証の結果)は App.tsx に
 * しか無く、これまで一切テストが無かった。
 *
 * 二重送信ガード(#391)は当初ここでも固定しようとしたが、送信中の再タップを
 * 実際に止めているのはカード側の表示状態で、App.tsx の2つのガード
 * (handleQuestClick / runQuestAction)のどちらを外してもテストが通ってしまった。
 * 「通るが何も守らないテスト」になるため、このファイルには含めていない。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { SettingsProvider } from './context/SettingsContext';
import { ToastProvider } from './context/ToastContext';
import type { Quest, User } from '@/types';

type CompleteResult = {
  success: boolean;
  status?: string;
  earnedMedals?: number;
  reason?: string;
  detail?: string;
};

const mocks = vi.hoisted(() => ({
  completeQuest: vi.fn(),
  play: vi.fn(),
}));

vi.mock('./hooks/useSound', () => ({ useSound: () => ({ play: mocks.play }) }));
vi.mock('./hooks/useRoutineData', () => ({
  useRoutineData: () => ({ flows: [], completeStep: vi.fn(), isCompleting: false }),
}));

const PARENT: User = {
  user_id: 'dad', name: 'パパ', role: 'role_adult',
  level: 1, exp: 0, gold: 0, job_class: '勇者', medal_count: 0,
} as User;

const QUEST: Quest = {
  quest_id: 101, title: 'おふろそうじ', quest_type: 'daily',
  exp_gain: 10, gold_gain: 20,
} as Quest;

vi.mock('./hooks/useGameData', () => ({
  useGameData: () => ({
    users: [PARENT],
    quests: [QUEST],
    rewards: [], completedQuests: [], pendingQuests: [],
    chronicle: null,
    isLoading: false,
    gameDataError: null, refetchGameData: vi.fn(),
    completeQuest: mocks.completeQuest, approveQuest: vi.fn(),
    rejectQuest: vi.fn(), cancelQuest: vi.fn(), buyReward: vi.fn(),
    refreshData: vi.fn(),
  }),
}));

import App from './App';

function renderApp() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <SettingsProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </SettingsProvider>
    </QueryClientProvider>
  );
}

const tapQuest = () => fireEvent.click(screen.getByText('おふろそうじ'));
const confirmYes = () => fireEvent.click(screen.getByRole('button', { name: 'はい' }));

beforeEach(() => {
  mocks.completeQuest.mockResolvedValue({ success: true } as CompleteResult);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('クエスト完了: 確認を挟む', () => {
  it('タップしただけでは完了APIを呼ばない(誤操作対策)', async () => {
    renderApp();
    tapQuest();

    // 確認モーダルは出るが、API はまだ呼ばれていない
    expect(await screen.findByRole('button', { name: 'はい' })).toBeInTheDocument();
    expect(mocks.completeQuest).not.toHaveBeenCalled();
  });

  it('「はい」で完了APIを呼び、モーダルを閉じる', async () => {
    renderApp();
    tapQuest();
    await screen.findByRole('button', { name: 'はい' });
    confirmYes();

    await waitFor(() => expect(mocks.completeQuest).toHaveBeenCalledTimes(1));
    expect(mocks.completeQuest.mock.calls[0][0].user_id).toBe('dad');
    expect(mocks.completeQuest.mock.calls[0][1].quest_id).toBe(101);
    await waitFor(() => expect(screen.queryByRole('button', { name: 'はい' })).toBeNull());
  });

  it('キャンセルすれば完了APIを呼ばない', async () => {
    renderApp();
    tapQuest();
    await screen.findByRole('button', { name: 'はい' });

    fireEvent.click(screen.getByRole('button', { name: /キャンセル|やめる|いいえ/ }));

    expect(mocks.completeQuest).not.toHaveBeenCalled();
  });
});

describe('クエスト完了: メダル演出', () => {
  it('メダルを獲得したらトーストで知らせる', async () => {
    mocks.completeQuest.mockResolvedValue({ success: true, earnedMedals: 2 } as CompleteResult);

    renderApp();
    tapQuest();
    await screen.findByRole('button', { name: 'はい' });
    confirmYes();

    // M-6-1 以前は earnedMedals を一切参照しておらず無反応だった
    expect(await screen.findByText('ちいさなメダルを 2 枚手に入れた！')).toBeInTheDocument();
  });

  it('メダル0枚ならメダルのトーストを出さない', async () => {
    mocks.completeQuest.mockResolvedValue({ success: true, earnedMedals: 0 } as CompleteResult);

    renderApp();
    tapQuest();
    await screen.findByRole('button', { name: 'はい' });
    confirmYes();

    await waitFor(() => expect(mocks.completeQuest).toHaveBeenCalled());
    expect(screen.queryByText(/ちいさなメダル/)).toBeNull();
  });
});

describe('クエスト完了: 失敗時', () => {
  it('エラーモーダルを出し、再試行でもう一度送る', async () => {
    mocks.completeQuest.mockResolvedValueOnce({
      success: false, reason: 'error', detail: 'サーバーが混み合っています',
    } as CompleteResult);

    renderApp();
    tapQuest();
    await screen.findByRole('button', { name: 'はい' });
    confirmYes();

    // バックエンドが返した detail をそのまま見せる(resolveErrorText)
    expect(await screen.findByText('サーバーが混み合っています')).toBeInTheDocument();

    mocks.completeQuest.mockResolvedValue({ success: true } as CompleteResult);
    fireEvent.click(screen.getByRole('button', { name: '再試行' }));

    await waitFor(() => expect(mocks.completeQuest).toHaveBeenCalledTimes(2));
  });

  it('再試行は確認モーダルを挟み直さない(同じ操作のやり直しのため)', async () => {
    mocks.completeQuest.mockResolvedValueOnce({
      success: false, reason: 'error', detail: 'boom',
    } as CompleteResult);

    renderApp();
    tapQuest();
    await screen.findByRole('button', { name: 'はい' });
    confirmYes();
    await screen.findByText('boom');

    mocks.completeQuest.mockResolvedValue({ success: true } as CompleteResult);
    fireEvent.click(screen.getByRole('button', { name: '再試行' }));

    await waitFor(() => expect(mocks.completeQuest).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole('button', { name: 'はい' })).toBeNull();
  });
});
