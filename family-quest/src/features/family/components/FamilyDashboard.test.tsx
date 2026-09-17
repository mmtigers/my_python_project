import { render, screen, cleanup, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import React from 'react';
import FamilyDashboard from './FamilyDashboard';
import { SettingsProvider } from '@/context/SettingsContext';
import { ToastProvider } from '@/context/ToastContext';
import { CompletedSignal, Quest, QuestHistory, User } from '@/types';

// Issue #659 改善案1(d): 横画面(Echo Show 15)の4人パネルは App が管理する
// 単一の completedSignal を全パネルへ配るため、「誰の完了か」を取り違えると
// 他の3人のパネルまで60秒 "Wait..." で固まる(#363 の実バグ)。
// QuestList 単体のテスト(QuestList.test.tsx)は1パネル分しか見ておらず、
// FamilyDashboard を実際に4人ぶん組み立てた状態での独立性は未検証だった。
// FamilyDashboard.tsx はカバレッジ0%だったため、この経路をここで固定する。

vi.mock('@/lib/apiClient', () => ({
    apiClient: {
        get: vi.fn(),
        post: vi.fn(),
    },
}));

import { apiClient } from '@/lib/apiClient';

const makeUser = (user_id: string, name: string, role: string): User => ({
    user_id, name, role, level: 1, exp: 0, gold: 100,
});

const dad = makeUser('dad', 'パパ', 'role_adult');
const mom = makeUser('mom', 'ママ', 'role_adult');
const son = makeUser('son', 'ともや', 'role_child');
const daughter = makeUser('daughter', 'ゆい', 'role_child');
const FAMILY = [dad, mom, son, daughter];

// target_user='all' なので4人全員のパネルに同じクエストが並ぶ。
const infiniteQuest: Quest = {
    quest_id: 10,
    title: '食器の片付け',
    quest_type: 'infinite',
    target_user: 'all',
    gold_gain: 5,
    icon_key: '🍽️',
};

// すごろく(RoutineFlow)は未開始にしておき、各パネルが QuestList を表示する状態にする。
const notStartedToday = {
    date: '2026-09-18',
    flows: {
        am: { started: false, title: '起きてから出発まで' },
        pm: { started: false, title: '帰ってから寝るまで' },
    },
};

function renderDashboard(
    completedSignal: CompletedSignal | null,
    pendingQuests: QuestHistory[] = [],
) {
    const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false, refetchInterval: false } },
    });
    const Wrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
        <QueryClientProvider client={queryClient}>
            <SettingsProvider>
                <ToastProvider>{children}</ToastProvider>
            </SettingsProvider>
        </QueryClientProvider>
    );
    return render(
        <Wrapper>
            <FamilyDashboard
                users={FAMILY}
                quests={[infiniteQuest]}
                completedQuests={[]}
                pendingQuests={pendingQuests}
                rewards={[]}
                onQuestClick={vi.fn()}
                onBuyReward={vi.fn()}
                onApprove={vi.fn()}
                onReject={vi.fn()}
                onApproveAll={vi.fn()}
                completedSignal={completedSignal}
                onAvatarClick={vi.fn()}
            />
        </Wrapper>
    );
}

// 指定した人の名前を表示しているパネル(FamilyPanel のルート要素)を返す。
const panelOf = (name: string): HTMLElement => {
    const nameNode = screen.getByText(name);
    const panel = nameNode.closest('div.flex.flex-col.bg-black\\/30');
    if (!panel) throw new Error(`${name} のパネルが見つかりません`);
    return panel as HTMLElement;
};

describe('FamilyDashboard 4パネルの独立クールダウン (#363 / #659)', () => {
    beforeEach(() => {
        window.localStorage.clear();
        vi.mocked(apiClient.get).mockResolvedValue(notStartedToday);
    });

    afterEach(() => {
        cleanup();
        vi.clearAllMocks();
        window.localStorage.clear();
    });

    it('4人ぶんのパネルを並べ、それぞれに同じ無限クエストを表示する', () => {
        renderDashboard(null);
        for (const user of FAMILY) {
            // 無限クエストの表示タイトルは「(N回目)」付き(useQuestStatus)
            expect(within(panelOf(user.name)).getByText('食器の片付け (1回目)')).toBeInTheDocument();
        }
        expect(screen.queryByText('Wait...')).not.toBeInTheDocument();
    });

    it('完了した本人のパネルだけがクールダウンに入る', () => {
        renderDashboard({ id: 10, userId: 'son', nonce: Date.now() });

        expect(within(panelOf('ともや')).getByText('Wait...')).toBeInTheDocument();
        // 他の3人は操作可能なまま
        for (const name of ['パパ', 'ママ', 'ゆい']) {
            expect(within(panelOf(name)).queryByText('Wait...')).not.toBeInTheDocument();
        }
        // 画面全体でも1件だけ
        expect(screen.getAllByText('Wait...')).toHaveLength(1);
    });

    it('別のクエストidのシグナルではどのパネルもクールダウンしない', () => {
        renderDashboard({ id: 99, userId: 'son', nonce: Date.now() });
        expect(screen.queryByText('Wait...')).not.toBeInTheDocument();
    });

    it('60秒より前に完了したシグナル(再マウント後の古い値)ではクールダウンしない', () => {
        // #567(b): タブ切替などで再マウントすると isCooldown は初期化される一方で
        // completedSignal は古いまま残る。経過時間を差し引いて判定する。
        renderDashboard({ id: 10, userId: 'son', nonce: Date.now() - 61000 });
        expect(screen.queryByText('Wait...')).not.toBeInTheDocument();
    });

    it('承認待ちがあれば、パネルとは別に承認バーを1つだけ表示する', () => {
        // 承認バーは各パネルではなくダッシュボード上部に1つだけ出る(親が代表)。
        renderDashboard(null);
        expect(screen.queryByText('承認待ちのアクション')).not.toBeInTheDocument();
        cleanup();

        renderDashboard(null, [
            { id: 1, user_id: 'son', quest_id: 10, status: 'pending', title: '食器の片付け' } as QuestHistory,
        ]);
        expect(screen.getAllByText('承認待ちのアクション')).toHaveLength(1);
    });
});
