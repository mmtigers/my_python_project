import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SettingsModal from './SettingsModal';
import { User } from '@/types';

const setDensity = vi.fn();
const toggleIconFirstUser = vi.fn();
const setUserThemeColor = vi.fn();
let settings = {
    density: 'comfortable' as 'comfortable' | 'compact',
    iconFirstUserIds: [] as string[],
    userThemeColors: {} as Record<string, string>,
};

vi.mock('@/context/useSettings', () => ({
    useSettings: () => ({
        ...settings,
        setDensity,
        toggleIconFirstUser,
        setUserThemeColor,
    }),
}));

const users: User[] = [
    { user_id: 'dad', name: 'とうさん', level: 1, exp: 0, gold: 0, avatar: '🧔' },
    { user_id: 'son', name: 'むすこ', level: 1, exp: 0, gold: 0, avatar: '/uploads/son.png' },
    { user_id: 'mom', name: 'かあさん', level: 1, exp: 0, gold: 0, avatar: null },
];

function renderModal(isOpen = true) {
    const onClose = vi.fn();
    render(<SettingsModal isOpen={isOpen} onClose={onClose} users={users} />);
    return { onClose };
}

afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    settings = { density: 'comfortable', iconFirstUserIds: [], userThemeColors: {} };
});

describe('SettingsModal', () => {
    it('閉じているときは何も描画しない', () => {
        renderModal(false);
        expect(screen.queryByText('表示せってい')).not.toBeInTheDocument();
    });

    it('表示密度を切り替えられる', () => {
        renderModal();
        fireEvent.click(screen.getByText('コンパクト'));
        expect(setDensity).toHaveBeenCalledWith('compact');
        fireEvent.click(screen.getByText('ゆったり'));
        expect(setDensity).toHaveBeenCalledWith('comfortable');
    });

    it('アイコン主体表示のチェックが現在の設定を反映する', () => {
        settings.iconFirstUserIds = ['son'];
        renderModal();
        const [dad, son] = screen.getAllByRole('checkbox') as HTMLInputElement[];
        expect(dad.checked).toBe(false);
        expect(son.checked).toBe(true);
    });

    it('チェックの切り替えで対象ユーザーを渡す', () => {
        renderModal();
        fireEvent.click(screen.getAllByRole('checkbox')[1]);
        expect(toggleIconFirstUser).toHaveBeenCalledWith('son');
    });

    // バックエンドに icon 列は無く avatar 列だけなので、user.icon を見ていた頃は
    // 全員が '🙂' 固定になっていた。アップロード済みはパスなので <img>、
    // それ以外は絵文字テキストで出し分ける。
    it('アバターがパスなら画像、絵文字ならテキスト、未設定なら既定の絵文字', () => {
        renderModal();
        expect(screen.getByRole('img', { name: 'むすこ' })).toHaveAttribute('src', '/uploads/son.png');
        expect(screen.getByText('🧔')).toBeInTheDocument();
        expect(screen.getByText('🙂')).toBeInTheDocument();
    });

    it('テーマカラーは全色をユーザーごとに出し、選ぶとユーザーと色を渡す', () => {
        renderModal();
        // 6色 × 3人
        expect(screen.getAllByLabelText('ブルー')).toHaveLength(users.length);
        fireEvent.click(screen.getAllByLabelText('グリーン')[0]);
        expect(setUserThemeColor).toHaveBeenCalledWith('dad', 'green');
    });
});
