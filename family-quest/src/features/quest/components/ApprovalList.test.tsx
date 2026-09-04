import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ApprovalList from './ApprovalList';
import { QuestHistory, User } from '@/types';

// #391(F-L8): 一括承認中・個別承認の応答待ち中の行は、承認/却下ボタンが押せず
// 「すべて承認」ボタンもローディング表示になることを検証する。

const users: User[] = [
    { user_id: 'dad', name: 'パパ', level: 1, exp: 0, gold: 0, role: 'role_adult' },
    { user_id: 'son', name: 'ともや', level: 1, exp: 0, gold: 0, role: 'role_child' },
];
const rows: QuestHistory[] = [
    { id: 1, user_id: 'son', quest_id: 10, quest_title: '歯みがき', status: 'pending', gold_earned: 5 },
    { id: 2, user_id: 'son', quest_id: 11, quest_title: 'おかたづけ', status: 'pending', gold_earned: 5 },
];

describe('ApprovalList busy state (#391 / F-L8)', () => {
    afterEach(() => {
        cleanup();
    });

    it('disables approve/reject for a busy row but keeps other rows interactive', () => {
        const onApprove = vi.fn();
        const onReject = vi.fn();
        render(
            <ApprovalList
                pendingQuests={rows}
                users={users}
                onApprove={onApprove}
                onReject={onReject}
                onApproveAll={vi.fn()}
                busyHistoryIds={[1]}
            />
        );

        const approveButtons = screen.getAllByRole('button', { name: /承認$/ });
        expect(approveButtons).toHaveLength(2);
        expect(approveButtons[0]).toBeDisabled();
        expect(approveButtons[1]).not.toBeDisabled();

        fireEvent.click(approveButtons[0]);
        expect(onApprove).not.toHaveBeenCalled();
        fireEvent.click(approveButtons[1]);
        expect(onApprove).toHaveBeenCalledWith(rows[1]);
    });

    it('shows the bulk approve button as loading while a bulk approval is running', () => {
        const onApproveAll = vi.fn();
        render(
            <ApprovalList
                pendingQuests={rows}
                users={users}
                onApprove={vi.fn()}
                onReject={vi.fn()}
                onApproveAll={onApproveAll}
                isApprovingAll
                busyHistoryIds={[1, 2]}
            />
        );
        const bulk = screen.getByRole('button', { name: /クエストをすべて承認/ });
        expect(bulk).toBeDisabled();
        fireEvent.click(bulk);
        expect(onApproveAll).not.toHaveBeenCalled();
    });
});
