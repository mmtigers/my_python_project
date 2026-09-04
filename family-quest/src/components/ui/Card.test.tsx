import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Card } from './Card';

// #412(F-L5): onClick付きのCardはキーボード操作(Tab移動 + Enter/Space)でも
// 操作できるようにする(role=button, tabIndex=0, Enter/Space→onClick)。
// onClickが無いCardには何も付与しない。

describe('Card keyboard accessibility (#412 F-L5)', () => {
    afterEach(() => {
        cleanup();
    });

    it('exposes role="button" and tabIndex=0 when onClick is given', () => {
        render(<Card onClick={vi.fn()}>content</Card>);
        const el = screen.getByRole('button');
        expect(el).toHaveAttribute('tabIndex', '0');
    });

    it('invokes onClick on Enter and Space keydown', () => {
        const onClick = vi.fn();
        render(<Card onClick={onClick}>content</Card>);
        const el = screen.getByRole('button');

        fireEvent.keyDown(el, { key: 'Enter' });
        expect(onClick).toHaveBeenCalledTimes(1);

        fireEvent.keyDown(el, { key: ' ' });
        expect(onClick).toHaveBeenCalledTimes(2);

        fireEvent.keyDown(el, { key: 'a' });
        expect(onClick).toHaveBeenCalledTimes(2);
    });

    it('still responds to a plain click', () => {
        const onClick = vi.fn();
        render(<Card onClick={onClick}>content</Card>);
        fireEvent.click(screen.getByRole('button'));
        expect(onClick).toHaveBeenCalledTimes(1);
    });

    it('does not add role/tabIndex when there is no onClick', () => {
        render(<Card>content</Card>);
        expect(screen.queryByRole('button')).not.toBeInTheDocument();
    });

    it('still calls a caller-provided onKeyDown alongside the Enter/Space handling', () => {
        const onClick = vi.fn();
        const onKeyDown = vi.fn();
        render(<Card onClick={onClick} onKeyDown={onKeyDown}>content</Card>);
        fireEvent.keyDown(screen.getByRole('button'), { key: 'Enter' });
        expect(onClick).toHaveBeenCalledTimes(1);
        expect(onKeyDown).toHaveBeenCalledTimes(1);
    });
});
