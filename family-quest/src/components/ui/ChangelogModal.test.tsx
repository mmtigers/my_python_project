import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ChangelogModal from './ChangelogModal';
import { CHANGELOG } from '@/lib/changelog';

describe('ChangelogModal', () => {
    afterEach(() => {
        cleanup();
    });

    it('renders nothing when closed', () => {
        render(<ChangelogModal isOpen={false} onClose={vi.fn()} />);
        expect(screen.queryByRole('dialog')).toBeNull();
    });

    it('shows every changelog entry with version, date and changes', () => {
        render(<ChangelogModal isOpen onClose={vi.fn()} />);
        expect(screen.getByRole('dialog', { name: 'アップデートのれきし' })).toBeInTheDocument();
        for (const entry of CHANGELOG) {
            expect(screen.getByText(`v${entry.version}`)).toBeInTheDocument();
            for (const change of entry.changes) {
                expect(screen.getByText(change)).toBeInTheDocument();
            }
        }
    });

    it('calls onClose from the close button', () => {
        const onClose = vi.fn();
        render(<ChangelogModal isOpen onClose={onClose} />);
        fireEvent.click(screen.getByRole('button', { name: '閉じる' }));
        expect(onClose).toHaveBeenCalledTimes(1);
    });
});

describe('CHANGELOG data', () => {
    it('has unique versions and is sorted newest first', () => {
        const versions = CHANGELOG.map(e => e.version);
        expect(new Set(versions).size).toBe(versions.length);
        const dates = CHANGELOG.map(e => e.date);
        expect(dates).toEqual([...dates].sort().reverse());
        for (const entry of CHANGELOG) {
            expect(entry.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
            expect(entry.changes.length).toBeGreaterThan(0);
        }
    });
});
