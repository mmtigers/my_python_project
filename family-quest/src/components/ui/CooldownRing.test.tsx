import { render, cleanup, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CooldownRing } from './CooldownRing';

// #477: durationMs<=0 が渡されると elapsed/durationMs のゼロ除算で
// Infinity/NaN経路を通り得た。早期リターンで明示的に完了状態(frac=0)に
// なることを確認する。
//
// Issue #659 改善案4: このファイルは実時間(setInterval + Date.now())に依存して
// いたため、進行の途中経過を検証できず「描画直後の一瞬」しか見ていなかった。
// vi.useFakeTimers() で時間を制御し、残り時間に応じたリングの推移まで固定する。

// 進捗リング(2つ目のcircle)の dasharray(=円周)と dashoffset を読む。
// frac=1(残り全部)なら dashoffset=0、frac=0(消費済み)なら dashoffset=円周。
const readRing = (container: HTMLElement) => {
    const progressCircle = container.querySelectorAll('circle')[1];
    return {
        dasharray: Number(progressCircle.getAttribute('stroke-dasharray')),
        dashoffset: Number(progressCircle.getAttribute('stroke-dashoffset')),
    };
};

describe('CooldownRing durationMs=0 (#477)', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        cleanup();
        vi.useRealTimers();
    });

    it('does not produce a NaN/Infinity stroke-dashoffset when durationMs is 0', () => {
        const { container } = render(<CooldownRing durationMs={0} size={40} />);
        expect(Number.isFinite(readRing(container).dashoffset)).toBe(true);
    });

    it('renders in the fully-depleted state immediately when durationMs is 0', () => {
        const { container } = render(<CooldownRing durationMs={0} size={40} />);
        const { dasharray, dashoffset } = readRing(container);
        // frac=0 のとき dashoffset は circumference(=dasharray) と一致する
        expect(dashoffset).toBeCloseTo(dasharray, 5);
    });

    it('still renders a normal in-progress ring for a positive duration', () => {
        const { container } = render(<CooldownRing durationMs={60000} size={40} />);
        const { dashoffset } = readRing(container);
        expect(Number.isFinite(dashoffset)).toBe(true);
        expect(dashoffset).toBeCloseTo(0, 5);
    });
});

describe('CooldownRing の残り時間の推移 (#659)', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        cleanup();
        vi.useRealTimers();
    });

    it('半分経過した時点で、リングがちょうど半分まで減っている', () => {
        const { container } = render(<CooldownRing durationMs={60000} size={40} />);
        act(() => {
            vi.advanceTimersByTime(30000);
        });
        const { dasharray, dashoffset } = readRing(container);
        expect(dashoffset).toBeCloseTo(dasharray / 2, 1);
    });

    it('経過に応じて単調に減っていく', () => {
        const { container } = render(<CooldownRing durationMs={60000} size={40} />);
        const readings: number[] = [];
        for (let i = 0; i < 4; i++) {
            act(() => {
                vi.advanceTimersByTime(10000);
            });
            readings.push(readRing(container).dashoffset);
        }
        for (let i = 1; i < readings.length; i++) {
            expect(readings[i]).toBeGreaterThan(readings[i - 1]);
        }
    });

    it('時間切れで完了状態(frac=0)になり、以降のタイマーは停止している', () => {
        const { container } = render(<CooldownRing durationMs={60000} size={40} />);
        act(() => {
            vi.advanceTimersByTime(60000);
        });
        const atEnd = readRing(container);
        expect(atEnd.dashoffset).toBeCloseTo(atEnd.dasharray, 5);
        // frac<=0 で clearInterval しているため、さらに進めても
        // マイナス側(dashoffset > 円周)へ振り切れない。
        expect(vi.getTimerCount()).toBe(0);
        act(() => {
            vi.advanceTimersByTime(60000);
        });
        expect(readRing(container).dashoffset).toBeCloseTo(atEnd.dasharray, 5);
    });

    it('アンマウント時にintervalを解放する', () => {
        const { unmount } = render(<CooldownRing durationMs={60000} size={40} />);
        expect(vi.getTimerCount()).toBeGreaterThan(0);
        unmount();
        expect(vi.getTimerCount()).toBe(0);
    });
});
