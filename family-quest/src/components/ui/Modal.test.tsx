import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Modal } from './Modal';

// #394: preventClose の間は背景タップ/ESC/×ボタンのいずれでも閉じられないこと、
// preventClose 解除後は従来通り閉じられることを検証する。あわせて role="dialog" /
// aria-modal が常に付与されることを確認する。

describe('Modal (#394)', () => {
    afterEach(() => {
        cleanup();
    });

    it('exposes role="dialog" and aria-modal="true"', () => {
        render(<Modal isOpen onClose={vi.fn()} title="タイトル">本文</Modal>);
        const dialog = screen.getByRole('dialog');
        expect(dialog).toHaveAttribute('aria-modal', 'true');
    });

    it('closes on backdrop click and Escape by default', () => {
        const onClose = vi.fn();
        const { container } = render(<Modal isOpen onClose={onClose}>本文</Modal>);

        const backdrop = container.querySelector('.absolute.inset-0.bg-black\\/80') as HTMLElement;
        fireEvent.click(backdrop);
        expect(onClose).toHaveBeenCalledTimes(1);

        fireEvent.keyDown(window, { key: 'Escape' });
        expect(onClose).toHaveBeenCalledTimes(2);
    });

    it('does not close on backdrop click, Escape, or the close button while preventClose is true', () => {
        const onClose = vi.fn();
        const { container } = render(
            <Modal isOpen onClose={onClose} preventClose title="確認">本文</Modal>
        );

        const backdrop = container.querySelector('.absolute.inset-0.bg-black\\/80') as HTMLElement;
        fireEvent.click(backdrop);
        fireEvent.keyDown(window, { key: 'Escape' });
        const closeButton = screen.getByRole('button');
        expect(closeButton).toBeDisabled();
        fireEvent.click(closeButton);

        expect(onClose).not.toHaveBeenCalled();
    });

    it('closes again once preventClose is lifted', () => {
        const onClose = vi.fn();
        const { rerender, container } = render(
            <Modal isOpen onClose={onClose} preventClose>本文</Modal>
        );
        fireEvent.keyDown(window, { key: 'Escape' });
        expect(onClose).not.toHaveBeenCalled();

        rerender(<Modal isOpen onClose={onClose}>本文</Modal>);
        const backdrop = container.querySelector('.absolute.inset-0.bg-black\\/80') as HTMLElement;
        fireEvent.click(backdrop);
        expect(onClose).toHaveBeenCalledTimes(1);
    });
});
