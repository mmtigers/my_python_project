import { render, screen, cleanup, fireEvent, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import QuestList from './QuestList';
import { getQuestProcessingKey } from '../hooks/useQuestStatus';
import { CompletedSignal, Quest, QuestHistory, User } from '@/types';

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

// #389: 長押し(取消)が閾値に達して取消APIが走り、その再取得で「完了済み」→「未完了」に
// 切り替わった直後、指を離した瞬間の click が同じカードの handleTapComplete に届いて
// 「完了確認モーダル」(onQuestClick)が開いてしまう競合の再現テスト。
describe('QuestList long-press cancel -> click race (#389)', () => {
    const dailyQuest: Quest = { quest_id: 20, title: '歯みがき', quest_type: 'daily', target_user: 'all', gold_gain: 3 };
    // 申請中(pending)のカードは折りたたまれず常時表示され、かつ長押し取消の対象になる
    const pending: QuestHistory = { id: 1, user_id: 'son', quest_id: 20, status: 'pending' };

    const renderList = (pendingRows: QuestHistory[], onQuestClick: (q: Quest) => void) => (
        <QuestList
            quests={[dailyQuest]}
            completedQuests={[]}
            pendingQuests={pendingRows}
            currentUser={son}
            onQuestClick={onQuestClick}
            completedSignal={null}
            panelMode
        />
    );

    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        cleanup();
        vi.useRealTimers();
    });

    it('ignores the click that lands right after a long-press cancel, but accepts a later tap', () => {
        const onQuestClick = vi.fn();
        const { rerender } = render(renderList([pending], onQuestClick));
        const title = screen.getByText('歯みがき');

        // 1. 申請中カードを長押し → 550ms で取消(onQuestClick 1回目)が発火
        fireEvent.pointerDown(title);
        act(() => {
            vi.advanceTimersByTime(550);
        });
        expect(onQuestClick).toHaveBeenCalledTimes(1);

        // 2. 取消APIの応答と再取得が指を離すより先に終わり、カードが「未申請」に切り替わる
        rerender(renderList([], onQuestClick));

        // 3. 指を離した瞬間の pointerup → click が同じカードに届く
        fireEvent.pointerUp(title);
        fireEvent.click(title);
        // 修正前はここで完了確認(onQuestClick 2回目)が開いていた
        expect(onQuestClick).toHaveBeenCalledTimes(1);

        // 4. 猶予時間を過ぎてからの通常タップは完了確認として受け付ける
        act(() => {
            vi.advanceTimersByTime(400);
        });
        fireEvent.click(title);
        expect(onQuestClick).toHaveBeenCalledTimes(2);
    });
});

// #391: 完了APIの送信中(processingQuestKeys に含まれる)カードは「送信中...」の
// オーバーレイを出し、再タップしても onQuestClick(確認モーダル)を呼ばない。
describe('QuestList in-flight processing overlay (#391)', () => {
    const dailyQuest: Quest = { quest_id: 30, title: 'おふろ', quest_type: 'daily', target_user: 'all', gold_gain: 3 };

    afterEach(() => {
        cleanup();
    });

    it('shows the busy overlay and ignores taps for the processing (user, quest) key only', () => {
        const onQuestClick = vi.fn();
        render(
            <QuestList
                quests={[dailyQuest]}
                completedQuests={[]}
                pendingQuests={[]}
                currentUser={son}
                onQuestClick={onQuestClick}
                completedSignal={null}
                processingQuestKeys={[getQuestProcessingKey('son', 30)]}
                panelMode
            />
        );
        expect(screen.getByText('送信中...')).toBeInTheDocument();
        fireEvent.click(screen.getByText('おふろ'));
        expect(onQuestClick).not.toHaveBeenCalled();
    });

    it('does not mark another user\'s panel as busy for the same quest', () => {
        const onQuestClick = vi.fn();
        render(
            <QuestList
                quests={[dailyQuest]}
                completedQuests={[]}
                pendingQuests={[]}
                currentUser={daughter}
                onQuestClick={onQuestClick}
                completedSignal={null}
                processingQuestKeys={[getQuestProcessingKey('son', 30)]}
                panelMode
            />
        );
        expect(screen.queryByText('送信中...')).not.toBeInTheDocument();
        fireEvent.click(screen.getByText('おふろ'));
        expect(onQuestClick).toHaveBeenCalledTimes(1);
    });
});
