import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import TvBlockBanner from './TvBlockBanner';
import { TvBlockState } from '../../types';

const base: TvBlockState = {
    is_blocked: false,
    blocked_until: null,
    next_block_starts_at: '12:00',
    seconds_until_next_block: 3 * 3600,
    windows: [{ start: '12:00', end: '14:00' }, { start: '20:00', end: null }],
};

describe('TvBlockBanner', () => {
    afterEach(cleanup);

    it('朝は今日のおやすみ時間を一覧で出す', () => {
        render(<TvBlockBanner tvBlock={base} tvSecondsLeft={3 * 3600} tvBlockActive={false} />);
        expect(screen.getByText('12:00〜14:00 と 20:00から')).toBeInTheDocument();
    });

    it('30分前から「あと◯分」を出す', () => {
        render(<TvBlockBanner tvBlock={base} tvSecondsLeft={25 * 60} tvBlockActive={false} />);
        expect(screen.getByText('あと25分')).toBeInTheDocument();
    });

    it('おやすみ中は終了時刻を、20時以降は「あしたまで」を出す', () => {
        const { rerender } = render(
            <TvBlockBanner tvBlock={{ ...base, is_blocked: true, blocked_until: '14:00' }} tvSecondsLeft={null} tvBlockActive />,
        );
        expect(screen.getByText('いまテレビおやすみ中')).toBeInTheDocument();
        expect(screen.getByText(/\(14:00まで\)/)).toBeInTheDocument();
        rerender(<TvBlockBanner tvBlock={{ ...base, is_blocked: true }} tvSecondsLeft={null} tvBlockActive />);
        expect(screen.getByText(/\(あしたまで\)/)).toBeInTheDocument();
    });
});
