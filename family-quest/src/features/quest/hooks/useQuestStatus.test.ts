import { describe, expect, it } from 'vitest';
import { getQuestLockState } from './useQuestStatus';
import { Quest, QuestHistory, User } from '@/types';

const user: User = { user_id: 'alice', name: 'Alice', level: 1, exp: 0, gold: 0 };

function makeQuest(overrides: Partial<Quest> = {}): Quest {
    return { id: 1, title: 'テストクエスト', ...overrides };
}

function makeHistory(overrides: Partial<QuestHistory> = {}): QuestHistory {
    return { user_id: 'alice', quest_id: 1, status: 'approved', ...overrides };
}

describe('getQuestLockState', () => {
    it('is unlocked, not done, not pending by default', () => {
        const state = getQuestLockState(makeQuest(), user, [], []);
        expect(state).toMatchObject({ isLocked: false, isDone: false, isPending: false, isInfinite: false });
    });

    it('locks the quest when its prerequisite is not yet approved for this user', () => {
        const quest = makeQuest({ pre_requisite_quest_id: 5 });
        const state = getQuestLockState(quest, user, [], []);
        expect(state.isLocked).toBe(true);
    });

    it('unlocks the quest once the prerequisite is approved for this user', () => {
        const quest = makeQuest({ pre_requisite_quest_id: 5 });
        const completed = [makeHistory({ quest_id: 5, status: 'approved' })];
        const state = getQuestLockState(quest, user, completed, []);
        expect(state.isLocked).toBe(false);
    });

    it('does not unlock via another user\'s approval of the prerequisite', () => {
        const quest = makeQuest({ pre_requisite_quest_id: 5 });
        const completed = [makeHistory({ user_id: 'bob', quest_id: 5, status: 'approved' })];
        const state = getQuestLockState(quest, user, completed, []);
        expect(state.isLocked).toBe(true);
    });

    it('does not unlock via a pending (not yet approved) prerequisite', () => {
        const quest = makeQuest({ pre_requisite_quest_id: 5 });
        const completed = [makeHistory({ quest_id: 5, status: 'pending' })];
        const state = getQuestLockState(quest, user, completed, []);
        expect(state.isLocked).toBe(true);
    });

    it('marks a quest done when this user has an approved completion', () => {
        const quest = makeQuest({ id: 1 });
        const completed = [makeHistory({ quest_id: 1 })];
        const state = getQuestLockState(quest, user, completed, []);
        expect(state.isDone).toBe(true);
        expect(state.completedEntry).toEqual(completed[0]);
    });

    it('prefers quest_id over id when matching history entries', () => {
        const quest = makeQuest({ id: 1, quest_id: 42 });
        const completed = [makeHistory({ quest_id: 42 })];
        const state = getQuestLockState(quest, user, completed, []);
        expect(state.isDone).toBe(true);
    });

    it('treats an infinite quest as never "done", even with completions', () => {
        const quest = makeQuest({ type: 'infinite' });
        const completed = [makeHistory(), makeHistory()];
        const state = getQuestLockState(quest, user, completed, []);
        expect(state.isInfinite).toBe(true);
        expect(state.isDone).toBe(false);
        expect(state.myCompletions).toHaveLength(2);
    });

    it('recognizes infinite via quest_type or the frontend _isInfinite flag too', () => {
        expect(getQuestLockState(makeQuest({ quest_type: 'infinite' }), user, [], []).isInfinite).toBe(true);
        expect(getQuestLockState(makeQuest({ _isInfinite: true }), user, [], []).isInfinite).toBe(true);
    });

    it('marks a quest pending when this user has a pending entry', () => {
        const quest = makeQuest({ id: 1 });
        const pending = [makeHistory({ quest_id: 1, status: 'pending' })];
        const state = getQuestLockState(quest, user, [], pending);
        expect(state.isPending).toBe(true);
        expect(state.pendingEntry).toEqual(pending[0]);
    });

    it('ignores another user\'s pending entry for the same quest', () => {
        const quest = makeQuest({ id: 1 });
        const pending = [makeHistory({ user_id: 'bob', quest_id: 1, status: 'pending' })];
        const state = getQuestLockState(quest, user, [], pending);
        expect(state.isPending).toBe(false);
        expect(state.pendingEntry).toBeUndefined();
    });
});
