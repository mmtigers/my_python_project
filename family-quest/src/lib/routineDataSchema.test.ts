import { describe, expect, it } from 'vitest';
import {
    isRoutineFlowBlocking,
    isRoutineFlowFreeTime,
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
    steps: [
        { key: 'wash', label: '顔を洗う', icon_key: 'wash', is_checkpoint: false, status: 'done' },
        { key: 'meal', label: '朝ごはん', icon_key: 'meal', is_checkpoint: false, status: 'current' },
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
