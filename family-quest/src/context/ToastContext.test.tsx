import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from './ToastContext';
import { useToast } from './useToast';

// #478: 手動dismiss後も自動非表示用のsetTimeoutが生き続け、既に無いtoastに対して
// 無駄なコールバックが走っていた問題の回帰テスト。
// framer-motionのexitアニメーション(AnimatePresence)はjsdom+fake timers環境では
// 完了しないため、DOM操作自体のテストとして本質的でないアニメーション部分を
// 素のdiv/フラグメントに差し替える。
vi.mock('framer-motion', () => ({
    AnimatePresence: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    motion: {
        div: React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
            (props, ref) => <div ref={ref} {...props} />
        ),
    },
}));

function TestHarness() {
    const { showToast } = useToast();
    return <button onClick={() => showToast({ title: 'Test Toast' })}>show</button>;
}

describe('ToastContext manual dismiss clears the auto-dismiss timer (#478)', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        cleanup();
        vi.useRealTimers();
    });

    it('clears the pending auto-dismiss timer when a toast is manually dismissed', () => {
        const clearTimeoutSpy = vi.spyOn(globalThis, 'clearTimeout');

        render(
            <ToastProvider>
                <TestHarness />
            </ToastProvider>
        );

        fireEvent.click(screen.getByText('show'));
        expect(screen.getByText('Test Toast')).toBeInTheDocument();

        // 手動dismiss(トースト自体をクリック)
        fireEvent.click(screen.getByText('Test Toast'));
        expect(screen.queryByText('Test Toast')).not.toBeInTheDocument();
        expect(clearTimeoutSpy).toHaveBeenCalled();
    });

    it('still auto-dismisses after AUTO_DISMISS_MS when not manually dismissed', () => {
        render(
            <ToastProvider>
                <TestHarness />
            </ToastProvider>
        );

        fireEvent.click(screen.getByText('show'));
        expect(screen.getByText('Test Toast')).toBeInTheDocument();

        act(() => {
            vi.advanceTimersByTime(4000);
        });

        expect(screen.queryByText('Test Toast')).not.toBeInTheDocument();
    });
});
