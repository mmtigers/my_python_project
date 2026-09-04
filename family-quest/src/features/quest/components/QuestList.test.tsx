import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import QuestList from './QuestList';
import { CompletedSignal, Quest, User } from '@/types';

// #363: 横画面4人パネルでは App が管理する同じ completedSignal が全パネルの
// QuestList に渡される。無限クエスト(target all)を兄が完了したとき、兄のパネル
// だけがクールダウン("Wait...")に入り、他のメンバーのパネルは操作可能なままで
// あることを検証する(サーバー側のクールダウンは (user, quest) 単位)。

const son: User = { user_id: 'son', name: 'ともや', level: 1, exp: 0, gold: 0, role: 'role_child' };
const daughter: User = { user_id: 'daughter', name: 'ゆい', level: 1, exp: 0, gold: 0, role: 'role_child' };

const infiniteQuest: Quest = {
    quest_id: 10,
    title: '食器の片付け',
    quest_type: 'infinite',
    target_user: 'all',
    gold_gain: 5,
    icon_key: '🍽️',
};

const renderPanel = (user: User, signal: CompletedSignal | null) =>
    render(
        <QuestList
            quests={[infiniteQuest]}
            completedQuests={[]}
            pendingQuests={[]}
            currentUser={user}
            onQuestClick={vi.fn()}
            completedSignal={signal}
            panelMode
        />
    );

describe('QuestList completedSignal cooldown (#363)', () => {
    afterEach(() => {
        cleanup();
    });

    it('puts the completing user\'s own panel into cooldown', () => {
        const signal: CompletedSignal = { id: 10, userId: 'son', nonce: 1 };
        renderPanel(son, signal);
        expect(screen.getByText('Wait...')).toBeInTheDocument();
    });

    it('does NOT lock the same infinite quest on another member\'s panel', () => {
        const signal: CompletedSignal = { id: 10, userId: 'son', nonce: 1 };
        renderPanel(daughter, signal);
        expect(screen.queryByText('Wait...')).not.toBeInTheDocument();
    });

    it('ignores a signal for a different quest id even for the same user', () => {
        const signal: CompletedSignal = { id: 99, userId: 'son', nonce: 1 };
        renderPanel(son, signal);
        expect(screen.queryByText('Wait...')).not.toBeInTheDocument();
    });

    it('does nothing without a signal', () => {
        renderPanel(son, null);
        expect(screen.queryByText('Wait...')).not.toBeInTheDocument();
    });
});
