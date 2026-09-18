/**
 * Issue #659 改善案1(b): `handleApproveAll` の部分失敗と `linked_history_id` カスケード。
 *
 * このハンドラの挙動は #119 / #238 / #391 / M-6-2 を経て作られたもので、
 * 「途中で止めない」「カスケードの相方は成功時のみスキップ」「再試行は最新の
 * pendingQuests から取り直す」のいずれも意図的な設計である。ここではそれを
 * App.tsx の描画経由(= 実際にユーザーがボタンを押す経路)で固定する。
 *
 * App.tsx はカバレッジ 0% の 684行コンポーネントなので、データ取得層
 * (useGameData / useRoutineData)と音だけを差し替え、それ以外は本物を描画する。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';

import { SettingsProvider } from './context/SettingsContext';
import { ToastProvider } from './context/ToastContext';
import type { QuestHistory, User } from '@/types';

// useGameData.approveQuest の戻り値。ActionResult とは別物で、兄妹連携クエストの
// カスケード承認で相方のメダル数(partnerEarnedMedals)を返す点が異なる(#238)。
type ApproveResult = {
  success: boolean;
  earnedMedals?: number;
  leveledUp?: boolean;
  partnerEarnedMedals?: number;
  reason?: string;
  detail?: string;
};

const mocks = vi.hoisted(() => ({
  approveQuest: vi.fn(),
  play: vi.fn(),
  pendingQuests: [] as QuestHistory[],
}));

vi.mock('./hooks/useSound', () => ({ useSound: () => ({ play: mocks.play }) }));
vi.mock('./hooks/useRoutineData', () => ({
  useRoutineData: () => ({ flows: [], completeStep: vi.fn(), isCompleting: false }),
}));

const PARENT: User = {
  user_id: 'dad', name: 'パパ', role: 'role_adult',
  level: 1, exp: 0, gold: 0, job_class: '勇者', medal_count: 0,
} as User;
const CHILD: User = {
  user_id: 'son', name: 'たろう', role: 'role_child',
  level: 1, exp: 0, gold: 0, job_class: '見習い', medal_count: 0,
} as User;

vi.mock('./hooks/useGameData', () => ({
  useGameData: () => ({
    users: [PARENT, CHILD],
    quests: [], rewards: [], completedQuests: [],
    pendingQuests: mocks.pendingQuests,
    chronicle: null,
    isLoading: false,
    gameDataError: null, refetchGameData: vi.fn(),
    completeQuest: vi.fn(), approveQuest: mocks.approveQuest,
    rejectQuest: vi.fn(), cancelQuest: vi.fn(), buyReward: vi.fn(),
    refreshData: vi.fn(),
  }),
}));

// useGameData のモック定義より後でないと、App が掴む参照が差し替わらない
import App from './App';

const history = (id: number, overrides: Partial<QuestHistory> = {}): QuestHistory => ({
  id, user_id: 'son', quest_id: 100 + id, quest_title: `クエスト${id}`,
  completed_at: '2026-01-01T00:00:00', status: 'pending',
  exp_earned: 10, gold_earned: 10, linked_history_id: null,
  ...overrides,
} as QuestHistory);

const ok = (extra: Partial<ApproveResult> = {}): ApproveResult => ({ success: true, ...extra });
const ng = (): ApproveResult => ({ success: false, reason: 'error', detail: 'boom' });

function renderApp(): ReactElement | void {
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

const approveAllButton = () => screen.getByRole('button', { name: /クエストをすべて承認/ });

beforeEach(() => {
  mocks.pendingQuests = [history(1), history(2), history(3)];
  mocks.approveQuest.mockResolvedValue(ok());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('一括承認: 全件成功', () => {
  it('承認待ちの全件を承認し、成功トーストを出す', async () => {
    renderApp();
    fireEvent.click(approveAllButton());

    await waitFor(() => expect(mocks.approveQuest).toHaveBeenCalledTimes(3));
    expect(await screen.findByText('3件のクエストを承認しました')).toBeInTheDocument();
    // 承認の記録名義は「代表の親」で固定(要件5)
    expect(mocks.approveQuest.mock.calls[0][0].user_id).toBe('dad');
  });
});

describe('一括承認: 部分失敗', () => {
  it('失敗しても途中で止めず、残りも試みる', async () => {
    mocks.approveQuest
      .mockResolvedValueOnce(ok())
      .mockResolvedValueOnce(ng())
      .mockResolvedValueOnce(ok());

    renderApp();
    fireEvent.click(approveAllButton());

    // 2件目が失敗しても3件目を試す(= break しない)
    await waitFor(() => expect(mocks.approveQuest).toHaveBeenCalledTimes(3));
  });

  it('成功件数を添えてエラーを出し、再試行できる', async () => {
    mocks.approveQuest
      .mockResolvedValueOnce(ok())
      .mockResolvedValueOnce(ng())
      .mockResolvedValueOnce(ng());

    renderApp();
    fireEvent.click(approveAllButton());

    expect(await screen.findByText('一部の承認に失敗しました (1/3件成功)')).toBeInTheDocument();
  });

  it('再試行は最新の承認待ち一覧を対象にする(承認済みを再送しない)', async () => {
    mocks.approveQuest
      .mockResolvedValueOnce(ok())
      .mockResolvedValueOnce(ng())
      .mockResolvedValueOnce(ng());

    renderApp();
    fireEvent.click(approveAllButton());
    await screen.findByText('一部の承認に失敗しました (1/3件成功)');

    // サーバー側で1件目が承認済みになった = 一覧から消えた状態を作る。
    // App が再描画されて初めて pendingQuestsRef が更新されるので、タブ切替で
    // App の state を動かして再描画させる(実機では refreshData 後の再取得に相当)。
    mocks.pendingQuests = [history(2), history(3)];
    mocks.approveQuest.mockClear();
    mocks.approveQuest.mockResolvedValue(ok());
    fireEvent.click(screen.getByRole('button', { name: 'ごほうび' }));
    fireEvent.click(screen.getByRole('button', { name: 'クエスト' }));
    await waitFor(() =>
      expect(screen.getByText(/承認待ちのアクション/).textContent).toContain('2')
    );

    fireEvent.click(screen.getByRole('button', { name: '再試行' }));

    // M-6-2: 古いクロージャを掴んでいると3件送って400になる
    await waitFor(() => expect(mocks.approveQuest).toHaveBeenCalledTimes(2));
    const sentIds = mocks.approveQuest.mock.calls.map((c) => c[1].id);
    expect(sentIds).toEqual([2, 3]);
  });
});

describe('一括承認: 兄妹連携クエストのカスケード', () => {
  it('成功した相方はAPIを呼ばずに成功として数える', async () => {
    // 1 と 2 が相互に連結している
    mocks.pendingQuests = [
      history(1, { linked_history_id: 2 }),
      history(2, { linked_history_id: 1 }),
      history(3),
    ];
    mocks.approveQuest.mockResolvedValue(ok());

    renderApp();
    fireEvent.click(approveAllButton());

    // 1 を承認するとサーバー側で 2 も承認される。2 は呼ばずに飛ばす
    await waitFor(() => expect(mocks.approveQuest).toHaveBeenCalledTimes(2));
    const sentIds = mocks.approveQuest.mock.calls.map((c) => c[1].id);
    expect(sentIds).toEqual([1, 3]);
    // それでも3件成功として扱う
    expect(await screen.findByText('3件のクエストを承認しました')).toBeInTheDocument();
  });

  it('連携の承認に失敗したら相方はスキップせず個別に試みる', async () => {
    mocks.pendingQuests = [
      history(1, { linked_history_id: 2 }),
      history(2, { linked_history_id: 1 }),
    ];
    // 1件目(連携の親)が失敗
    mocks.approveQuest.mockResolvedValueOnce(ng()).mockResolvedValueOnce(ok());

    renderApp();
    fireEvent.click(approveAllButton());

    // カスケードは起きていないので 2 も個別に送る(失敗時にスキップすると取り残される)
    await waitFor(() => expect(mocks.approveQuest).toHaveBeenCalledTimes(2));
    const sentIds = mocks.approveQuest.mock.calls.map((c) => c[1].id);
    expect(sentIds).toEqual([1, 2]);
  });

  it('相方側のメダルも合算してトーストに出す', async () => {
    mocks.pendingQuests = [
      history(1, { linked_history_id: 2 }),
      history(2, { linked_history_id: 1 }),
    ];
    mocks.approveQuest.mockResolvedValue(ok({ earnedMedals: 1, partnerEarnedMedals: 2 }));

    renderApp();
    fireEvent.click(approveAllButton());

    // #238: 相方のメダルを取りこぼさない(1 + 2 = 3枚)
    expect(await screen.findByText('ちいさなメダルを 3 枚手に入れた！')).toBeInTheDocument();
  });
});

describe('一括承認: 二重送信の防止', () => {
  it('処理中に押し直しても2周目を始めない', async () => {
    let release: (value: ApproveResult) => void = () => {};
    mocks.approveQuest.mockImplementation(
      () => new Promise<ApproveResult>((resolve) => { release = resolve; })
    );

    renderApp();
    fireEvent.click(approveAllButton());
    await waitFor(() => expect(mocks.approveQuest).toHaveBeenCalledTimes(1));

    // #119: 応答待ちの間に連打しても、同じ履歴をもう一度承認しに行かない
    fireEvent.click(approveAllButton());
    fireEvent.click(approveAllButton());
    expect(mocks.approveQuest).toHaveBeenCalledTimes(1);

    release(ok());
  });
});

describe('一括承認: 表示条件', () => {
  it('承認待ちが1件だけならボタンを出さない', () => {
    mocks.pendingQuests = [history(1)];
    renderApp();

    expect(screen.queryByRole('button', { name: /クエストをすべて承認/ })).toBeNull();
  });

  it('承認待ちが無ければ承認セクション自体を出さない', () => {
    mocks.pendingQuests = [];
    renderApp();

    expect(screen.queryByText(/承認待ちのアクション/)).toBeNull();
  });
});
