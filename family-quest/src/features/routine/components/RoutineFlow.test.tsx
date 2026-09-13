import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import RoutineFlow, { RoutineFreeTimeBanner } from './RoutineFlow';
import { RoutineActiveFlow } from '@/lib/routineDataSchema';

const amFlow: RoutineActiveFlow = {
    started: true,
    title: '起きてから出発まで',
    checkpoint_time: '07:50',
    current_step_index: 0,
    in_free_time: false,
    is_complete: false,
    bonus_gold: 0,
    bonus_exp: 0,
    preview_bonus_gold: 50,
    bonus_full_gold: 150,
    leveled_up: false,
    new_level: null,
    steps: [
        { key: 'wash', label: '顔を洗う', icon_key: 'wash', is_checkpoint: false, is_checklist: true, status: 'done' },
        { key: 'meal', label: '朝ごはん', icon_key: 'meal', is_checkpoint: false, is_checklist: true, status: 'current' },
        { key: 'clothes', label: '着替える', icon_key: 'clothes', is_checkpoint: false, is_checklist: true, status: 'remind' },
        { key: 'free', label: '自由時間', icon_key: 'free', is_checkpoint: true, is_checklist: false, status: 'locked' },
        { key: 'leave', label: '出発', icon_key: 'leave', is_checkpoint: false, is_checklist: false, status: 'locked' },
    ],
};

describe('RoutineFlow', () => {
    afterEach(() => cleanup());

    it('renders checklist items as tappable rows that call onCompleteStep with their key', () => {
        const onCompleteStep = vi.fn();
        render(<RoutineFlow flowKey="am" flow={amFlow} onCompleteStep={onCompleteStep} />);

        expect(screen.getByText('朝ごはん')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /朝ごはん/ }));
        expect(onCompleteStep).toHaveBeenCalledWith('meal');
    });

    it('lets an already-checked checklist item be tapped again to toggle it off', () => {
        const onCompleteStep = vi.fn();
        render(<RoutineFlow flowKey="am" flow={amFlow} onCompleteStep={onCompleteStep} />);

        fireEvent.click(screen.getByRole('button', { name: /顔を洗う/ }));
        expect(onCompleteStep).toHaveBeenCalledWith('wash');
    });

    it('shows the live departure-bonus preview alongside the checklist', () => {
        render(<RoutineFlow flowKey="am" flow={amFlow} onCompleteStep={vi.fn()} />);
        expect(screen.getByText('出発ボーナス 50 / 150')).toBeInTheDocument();
    });

    it('shows the gentle "まだだよ" reminder for a forced-skipped step instead of a penalty', () => {
        render(<RoutineFlow flowKey="am" flow={amFlow} onCompleteStep={vi.fn()} />);
        expect(screen.getByText('まだだよ')).toBeInTheDocument();
        expect(screen.getByText('着替える')).toBeInTheDocument();
    });

    it('shows the checkpoint time on the checkpoint segment', () => {
        render(<RoutineFlow flowKey="am" flow={amFlow} onCompleteStep={vi.fn()} />);
        expect(screen.getByText('07:50')).toBeInTheDocument();
    });

    it('renders nothing once the flow has not started today', () => {
        const { container } = render(
            <RoutineFlow flowKey="am" flow={{ started: false, title: '起きてから出発まで' }} onCompleteStep={vi.fn()} />
        );
        expect(container).toBeEmptyDOMElement();
    });

    it('shows a completion card once every step is done', () => {
        render(<RoutineFlow flowKey="am" flow={{ ...amFlow, is_complete: true }} onCompleteStep={vi.fn()} />);
        expect(screen.getByText('起きてから出発まで・完了！')).toBeInTheDocument();
    });

    it('hands off to the free-time banner during in_free_time instead of showing the rail', () => {
        render(<RoutineFlow flowKey="am" flow={{ ...amFlow, in_free_time: true }} onCompleteStep={vi.fn()} />);
        expect(screen.getByText('自由時間中')).toBeInTheDocument();
        expect(screen.queryByText('着替える')).not.toBeInTheDocument();
    });
});

describe('RoutineFreeTimeBanner', () => {
    afterEach(() => cleanup());

    it('shows the checkpoint time as the next transition', () => {
        render(<RoutineFreeTimeBanner flowKey="am" flow={{ ...amFlow, in_free_time: true }} />);
        expect(screen.getByText('07:50 になったら次へ進むよ')).toBeInTheDocument();
    });

    it('renders nothing when not currently in free time', () => {
        const { container } = render(<RoutineFreeTimeBanner flowKey="am" flow={amFlow} />);
        expect(container).toBeEmptyDOMElement();
    });
});
