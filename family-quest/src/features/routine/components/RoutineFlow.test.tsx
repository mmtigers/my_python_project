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

// 寝る準備(晩ごはん・お風呂・着替え・歯磨き)はチェックポイント(自由時間)より後に
// 置かれたチェックリスト。amの朝の準備(チェックポイントより前)と異なり、出発
// ボーナスとは無関係になるため、ボーナス表示チップが出ないことも合わせて確認する。
const pmFlow: RoutineActiveFlow = {
    started: true,
    title: '帰ってから寝るまで',
    checkpoint_time: '18:00',
    current_step_index: 5,
    in_free_time: false,
    is_complete: false,
    bonus_gold: 150,
    bonus_exp: 30,
    preview_bonus_gold: 150,
    bonus_full_gold: 150,
    leveled_up: false,
    new_level: null,
    steps: [
        { key: 'handwash', label: '手洗い・うがい', icon_key: 'handwash', is_checkpoint: false, is_checklist: false, status: 'done' },
        { key: 'snack', label: 'おやつ休憩', icon_key: 'snack', is_checkpoint: false, is_checklist: false, status: 'done' },
        { key: 'homework', label: '宿題', icon_key: 'homework', is_checkpoint: false, is_checklist: false, status: 'done' },
        { key: 'tomorrow_prep', label: '明日の準備', icon_key: 'tomorrow_prep', is_checkpoint: false, is_checklist: false, status: 'done' },
        { key: 'free', label: '自由時間', icon_key: 'free', is_checkpoint: true, is_checklist: false, status: 'done' },
        { key: 'dinner', label: '晩ごはん', icon_key: 'meal', is_checkpoint: false, is_checklist: true, status: 'current' },
        { key: 'bath', label: 'お風呂', icon_key: 'bath', is_checkpoint: false, is_checklist: true, status: 'done' },
        { key: 'nightclothes', label: '着替え', icon_key: 'clothes', is_checkpoint: false, is_checklist: true, status: 'current' },
        { key: 'nightteeth', label: '歯磨き', icon_key: 'teeth', is_checkpoint: false, is_checklist: true, status: 'current' },
        { key: 'sleep', label: '就寝', icon_key: 'sleep', is_checkpoint: false, is_checklist: false, status: 'locked' },
    ],
};

describe('RoutineFlow (pm evening checklist)', () => {
    afterEach(() => cleanup());

    it('renders the checklist block between the preceding and following path steps, not always first', () => {
        render(<RoutineFlow flowKey="pm" flow={pmFlow} onCompleteStep={vi.fn()} />);
        const labels = screen.getAllByText(/手洗い・うがい|自由時間|晩ごはん|就寝/).map((el) => el.textContent);
        // '自由時間'(チェックポイント直前の一本道)は寝る準備チェックリストより前、
        // '就寝'(チェックリスト後続の一本道)はチェックリストより後に描画される。
        expect(labels.indexOf('自由時間')).toBeLessThan(labels.indexOf('晩ごはん'));
        expect(labels.indexOf('就寝')).toBeGreaterThan(labels.indexOf('晩ごはん'));
    });

    it('lets night checklist items be tapped in any order', () => {
        const onCompleteStep = vi.fn();
        render(<RoutineFlow flowKey="pm" flow={pmFlow} onCompleteStep={onCompleteStep} />);
        fireEvent.click(screen.getByRole('button', { name: /お風呂/ }));
        expect(onCompleteStep).toHaveBeenCalledWith('bath');
    });

    it('does not show a departure-bonus chip for a checklist positioned after the checkpoint', () => {
        render(<RoutineFlow flowKey="pm" flow={pmFlow} onCompleteStep={vi.fn()} />);
        expect(screen.queryByText(/出発ボーナス/)).not.toBeInTheDocument();
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
