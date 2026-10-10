import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Header from './Header';

const baseProps = {
    users: [],
    currentUserIdx: 0,
    viewMode: 'user' as const,
    onUserSwitch: vi.fn(),
    onLogSwitch: vi.fn(),
    onSettingsClick: vi.fn(),
    onChangelogClick: vi.fn(),
};

describe('Header', () => {
    afterEach(cleanup);

    it('notices を渡すと、ボタン行と同じヘッダー内におしらせを描画する', () => {
        const { container } = render(<Header {...baseProps} notices={<div>テレビおやすみ</div>} />);
        expect(container.querySelector('header')).toContainElement(screen.getByText('テレビおやすみ'));
    });

    it('おしらせが空のときは、余白だけが残らないよう枠を隠す(empty:hidden)', () => {
        const { container } = render(<Header {...baseProps} notices={<>{null}</>} />);
        const noticeSlot = container.querySelector('header .empty\\:hidden');
        expect(noticeSlot).not.toBeNull();
        expect(noticeSlot).toBeEmptyDOMElement();
    });

    it('未読のアップデートがあるときだけ履歴ボタンに NEW バッジを付ける', () => {
        const { rerender } = render(<Header {...baseProps} />);
        expect(screen.queryByText('NEW')).toBeNull();
        rerender(<Header {...baseProps} hasNewUpdate />);
        expect(screen.getByText('NEW')).toBeInTheDocument();
    });
});
