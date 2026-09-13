import { describe, expect, it } from 'vitest';
import {
    isRoutineFlowBlocking,
    isRoutineFlowFreeTime,
    selectRoutineFlow,
    RoutineFlowState,
} from './routineDataSchema';

const notStarted: RoutineFlowState = { started: false, title: '起きてから出発まで' };

const activeFlow: RoutineFlowState = {
    started: true,
    title: '起きてから出発まで',
    checkpoint_time: '07:50',
    current_step_index: 1,
    in_free_time: false,
    is_complete: false,
    bonus_gold: 0,
    bonus_exp: 0,
    preview_bonus_gold: 0,
    bonus_full_gold: 150,
    leveled_up: false,
    new_level: null,
    steps: [
        { key: 'wash', label: '顔を洗う', icon_key: 'wash', is_checkpoint: false, is_checklist: true, status: 'done' },
        { key: 'meal', label: '朝ごはん', icon_key: 'meal', is_checkpoint: false, is_checklist: true, status: 'current' },
    ],
};

const freeTimeFlow: RoutineFlowState = { ...activeFlow, in_free_time: true };
const completeFlow: RoutineFlowState = { ...activeFlow, is_complete: true, current_step_index: 2 };

describe('isRoutineFlowBlocking', () => {
    it('is false when the flow has not started yet today', () => {
        expect(isRoutineFlowBlocking(notStarted)).toBe(false);
    });

    it('is true while a step is in progress', () => {
        expect(isRoutineFlowBlocking(activeFlow)).toBe(true);
    });

    it('is false during free time (hands off to the quest picker)', () => {
        expect(isRoutineFlowBlocking(freeTimeFlow)).toBe(false);
    });

    it('is false once the flow is complete', () => {
        expect(isRoutineFlowBlocking(completeFlow)).toBe(false);
    });

    it('is false for undefined', () => {
        expect(isRoutineFlowBlocking(undefined)).toBe(false);
    });
});

describe('isRoutineFlowFreeTime', () => {
    it('is true only while in_free_time', () => {
        expect(isRoutineFlowFreeTime(freeTimeFlow)).toBe(true);
        expect(isRoutineFlowFreeTime(activeFlow)).toBe(false);
        expect(isRoutineFlowFreeTime(notStarted)).toBe(false);
    });
});

describe('selectRoutineFlow', () => {
    it('returns nulls when flows are not loaded yet', () => {
        expect(selectRoutineFlow(undefined)).toEqual({ activeKey: null, freeTimeKey: null });
    });

    it('prefers am over pm when both flows are actively blocking', () => {
        expect(selectRoutineFlow({ am: activeFlow, pm: activeFlow })).toEqual({ activeKey: 'am', freeTimeKey: null });
    });

    it('falls back to pm when only pm is blocking', () => {
        expect(selectRoutineFlow({ am: notStarted, pm: activeFlow })).toEqual({ activeKey: 'pm', freeTimeKey: null });
    });

    it('reports free time only when nothing is actively blocking, am preferred', () => {
        expect(selectRoutineFlow({ am: freeTimeFlow, pm: freeTimeFlow })).toEqual({ activeKey: null, freeTimeKey: 'am' });
    });

    it('never reports free time while a flow is blocking, even if the other is in free time', () => {
        expect(selectRoutineFlow({ am: activeFlow, pm: freeTimeFlow })).toEqual({ activeKey: 'am', freeTimeKey: null });
    });

    it('returns nulls when neither flow is started', () => {
        expect(selectRoutineFlow({ am: notStarted, pm: notStarted })).toEqual({ activeKey: null, freeTimeKey: null });
    });
});
