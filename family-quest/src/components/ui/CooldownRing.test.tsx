import { render, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { CooldownRing } from './CooldownRing';

// #477: durationMs<=0 が渡されると elapsed/durationMs のゼロ除算で
// Infinity/NaN経路を通り得た。早期リターンで明示的に完了状態(frac=0)に
// なることを確認する。

describe('CooldownRing durationMs=0 (#477)', () => {
    afterEach(() => {
        cleanup();
    });

    it('does not produce a NaN/Infinity stroke-dashoffset when durationMs is 0', () => {
        const { container } = render(<CooldownRing durationMs={0} size={40} />);
        const progressCircle = container.querySelectorAll('circle')[1];
        const dashoffset = Number(progressCircle.getAttribute('stroke-dashoffset'));
        expect(Number.isFinite(dashoffset)).toBe(true);
    });

    it('renders in the fully-depleted state immediately when durationMs is 0', () => {
        const { container } = render(<CooldownRing durationMs={0} size={40} />);
        const progressCircle = container.querySelectorAll('circle')[1];
        const dasharray = Number(progressCircle.getAttribute('stroke-dasharray'));
        const dashoffset = Number(progressCircle.getAttribute('stroke-dashoffset'));
        // frac=0 のとき dashoffset は circumference(=dasharray) と一致する
        expect(dashoffset).toBeCloseTo(dasharray, 5);
    });

    it('still renders a normal in-progress ring for a positive duration', () => {
        const { container } = render(<CooldownRing durationMs={60000} size={40} />);
        const progressCircle = container.querySelectorAll('circle')[1];
        const dashoffset = Number(progressCircle.getAttribute('stroke-dashoffset'));
        expect(Number.isFinite(dashoffset)).toBe(true);
        expect(dashoffset).toBeCloseTo(0, 5);
    });
});
