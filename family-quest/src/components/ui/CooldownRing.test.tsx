import { render, cleanup, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CooldownRing } from './CooldownRing';

/**
 * #477: durationMs<=0 が渡されると elapsed/durationMs のゼロ除算で
 * Infinity/NaN経路を通り得た。早期リターンで明示的に完了状態(frac=0)に
 * なることを確認する。
 *
 * #659 改善案4: 以前は t=0 のスナップショットしか見ておらず、
 *   - 実時間に依存していた(100msのintervalが走る前に読む前提)
 *   - カウントダウンそのもの(このコンポーネントの存在理由)が未検証だった
 * ため、`vi.useFakeTimers()` を入れて時間を進める側も固定する。
 */

/** 進捗円(2枚目のcircle)の dashoffset / dasharray を読む。 */
function ring(container: HTMLElement) {
    const progressCircle = container.querySelectorAll('circle')[1];
    const dashoffset = Number(progressCircle.getAttribute('stroke-dashoffset'));
    const dasharray = Number(progressCircle.getAttribute('stroke-dasharray'));
    // dashoffset は circumference * (1 - 残り割合)。生の長さで比べると size 依存に
    // なるので、割合に直してから比べる。
    return { dashoffset, dasharray, elapsedFraction: dashoffset / dasharray };
}

beforeEach(() => {
    vi.useFakeTimers();
});

afterEach(() => {
    cleanup();
    vi.useRealTimers();
});

describe('CooldownRing durationMs=0 (#477)', () => {
    it('does not produce a NaN/Infinity stroke-dashoffset when durationMs is 0', () => {
        const { container } = render(<CooldownRing durationMs={0} size={40} />);
        expect(Number.isFinite(ring(container).dashoffset)).toBe(true);
    });

    it('renders in the fully-depleted state immediately when durationMs is 0', () => {
        const { container } = render(<CooldownRing durationMs={0} size={40} />);
        const { dashoffset, dasharray } = ring(container);
        // frac=0 のとき dashoffset は circumference(=dasharray) と一致する
        expect(dashoffset).toBeCloseTo(dasharray, 5);
    });

    it('still renders a normal in-progress ring for a positive duration', () => {
        const { container } = render(<CooldownRing durationMs={60000} size={40} />);
        const { dashoffset } = ring(container);
        expect(Number.isFinite(dashoffset)).toBe(true);
        expect(dashoffset).toBeCloseTo(0, 5);
    });

    it('durationMs=0 ではintervalを張らない(時間を進めても何も起きない)', () => {
        const { container } = render(<CooldownRing durationMs={0} size={40} />);
        const before = ring(container).dashoffset;

        act(() => { vi.advanceTimersByTime(10_000); });

        expect(ring(container).dashoffset).toBeCloseTo(before, 5);
    });
});

describe('CooldownRing のカウントダウン (#659)', () => {
    it('時間が経つほどリングが減っていく', () => {
        const { container } = render(<CooldownRing durationMs={1000} size={40} />);
        expect(ring(container).elapsedFraction).toBeCloseTo(0, 5);

        // 更新は100ms間隔なので、時間は100msの倍数で進めて比べる
        act(() => { vi.advanceTimersByTime(200); });
        expect(ring(container).elapsedFraction).toBeCloseTo(0.2, 5);

        act(() => { vi.advanceTimersByTime(500); });
        expect(ring(container).elapsedFraction).toBeCloseTo(0.7, 5);
    });

    it('更新はintervalの刻み(100ms)でしか進まない', () => {
        // 刻みの途中(250ms)では、直前の刻み(200ms)の値のままになる。
        const { container } = render(<CooldownRing durationMs={1000} size={40} />);

        act(() => { vi.advanceTimersByTime(250); });

        expect(ring(container).elapsedFraction).toBeCloseTo(0.2, 5);
    });

    it('経過しきると完了状態(frac=0)で止まる', () => {
        const { container } = render(<CooldownRing durationMs={1000} size={40} />);

        act(() => { vi.advanceTimersByTime(1000); });
        expect(ring(container).elapsedFraction).toBeCloseTo(1, 5);

        // frac<=0 で clearInterval しているので、さらに進めても行き過ぎない
        act(() => { vi.advanceTimersByTime(10_000); });
        expect(ring(container).elapsedFraction).toBeCloseTo(1, 5);
    });

    it('完了後はintervalが残らない', () => {
        render(<CooldownRing durationMs={1000} size={40} />);

        act(() => { vi.advanceTimersByTime(1000); });

        expect(vi.getTimerCount()).toBe(0);
    });

    it('アンマウントでintervalを解除する(残り時間があっても)', () => {
        const { unmount } = render(<CooldownRing durationMs={60_000} size={40} />);
        expect(vi.getTimerCount()).toBe(1);

        unmount();

        expect(vi.getTimerCount()).toBe(0);
    });

    it('durationMs が変わると新しい残り時間で数え直す', () => {
        const { container, rerender } = render(<CooldownRing durationMs={1000} size={40} />);
        act(() => { vi.advanceTimersByTime(500); });
        expect(ring(container).elapsedFraction).toBeCloseTo(0.5, 5);

        rerender(<CooldownRing durationMs={2000} size={40} />);

        // 見た目は次の刻みまで前の値のまま。effect は startedAt を取り直すだけで
        // remainingFraction を1に戻さないため(実運用では durationMs は 60000 固定で
        // 変わらないので問題にならない)。
        expect(ring(container).elapsedFraction).toBeCloseTo(0.5, 5);

        // 次の刻みからは新しい durationMs と新しい startedAt で数え直す
        act(() => { vi.advanceTimersByTime(500); });
        expect(ring(container).elapsedFraction).toBeCloseTo(0.25, 5);
    });
});
