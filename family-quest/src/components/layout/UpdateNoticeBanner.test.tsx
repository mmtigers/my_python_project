import { cleanup, fireEvent, render, renderHook, act, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import UpdateNoticeBanner from './UpdateNoticeBanner';
import { useChangelogUnread } from '../../hooks/useChangelogUnread';
import { CHANGELOG } from '../../lib/changelog';

describe('UpdateNoticeBanner', () => {
    afterEach(cleanup);

    it('版数を出し、「見る」「閉じる」を呼び出し側に通知する', () => {
        const onOpen = vi.fn();
        const onDismiss = vi.fn();
        render(<UpdateNoticeBanner entry={CHANGELOG[0]} onOpen={onOpen} onDismiss={onDismiss} />);
        expect(screen.getByText('アップデートがありました')).toBeInTheDocument();
        expect(screen.getByText(`(v${CHANGELOG[0].version})`)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: '見る' }));
        fireEvent.click(screen.getByRole('button', { name: 'アップデートのお知らせを閉じる' }));
        expect(onOpen).toHaveBeenCalledTimes(1);
        expect(onDismiss).toHaveBeenCalledTimes(1);
    });
});

describe('useChangelogUnread', () => {
    beforeEach(() => window.localStorage.clear());
    afterEach(() => vi.restoreAllMocks());

    it('まだ何も見ていない端末では未読で、markSeenで既読になり、再マウントしても既読のまま', () => {
        const first = renderHook(() => useChangelogUnread());
        expect(first.result.current.hasUnread).toBe(true);
        act(() => first.result.current.markSeen());
        expect(first.result.current.hasUnread).toBe(false);
        first.unmount();

        expect(renderHook(() => useChangelogUnread()).result.current.hasUnread).toBe(false);
    });

    it('古い版を見たままの端末は、新しい版が出たら再び未読になる', () => {
        window.localStorage.setItem('familyQuest.changelog.seenVersion', '0.0.1');
        expect(renderHook(() => useChangelogUnread()).result.current.hasUnread).toBe(true);
    });

    it('localStorageが使えなくても例外にせず、未読扱いになる', () => {
        vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
        vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
        const { result } = renderHook(() => useChangelogUnread());
        expect(result.current.hasUnread).toBe(true);
        expect(() => act(() => result.current.markSeen())).not.toThrow();
    });
});
