import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import FamilyLog from './FamilyLog';
import { ChronicleItem } from '@/hooks/useGameData';
import { User } from '@/types';

// framer-motion の onPanEnd は jsdom では発火しないので、
// スワイプ判定だけを呼び出せる最小の差し替えにする。
vi.mock('framer-motion', () => ({
    motion: {
        div: ({ children, onPanEnd, ...rest }: {
            children?: React.ReactNode;
            onPanEnd?: (e: unknown, info: { offset: { x: number } }) => void;
            className?: string;
        }) => (
            <div
                {...rest}
                data-testid="pan-area"
                onMouseUp={e => onPanEnd?.(e, {
                    offset: { x: Number((e.target as HTMLElement).dataset.offsetX ?? 0) },
                })}
            >
                {children}
            </div>
        ),
    },
}));

const users: User[] = [
    { user_id: 'dad', name: 'とうさん', level: 1, exp: 0, gold: 0 },
    { user_id: 'son', name: 'むすこ', level: 1, exp: 0, gold: 0 },
    { user_id: 'mom', name: 'かあさん', level: 1, exp: 0, gold: 0 },
];

const log = (over: Partial<ChronicleItem>): ChronicleItem => ({
    timestamp: '2026-09-21T09:30:00',
    dateStr: '2026/09/21',
    ...over,
});

function swipe(offsetX: number) {
    const area = screen.getByTestId('pan-area');
    area.dataset.offsetX = String(offsetX);
    fireEvent.mouseUp(area, { target: area });
}

function setViewport(isTabletOrWider: boolean) {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: isTabletOrWider })));
}

beforeEach(() => setViewport(false)); // 既定はスマホ幅
afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
});

describe('FamilyLog', () => {
    it('読み込み前は読み込み中を出す', () => {
        render(<FamilyLog chronicle={undefined as unknown as ChronicleItem[]} users={users} />);
        expect(screen.getByText('冒険の記録を読み込んでいます...')).toBeInTheDocument();
    });

    it('ユーザーごとに自分の記録だけを出す', () => {
        render(
            <FamilyLog
                chronicle={[
                    log({ userId: 'dad', text: 'とうさんの記録' }),
                    log({ userId: 'son', text: 'むすこの記録' }),
                ]}
                users={users}
            />,
        );
        expect(screen.getByText('とうさんの記録')).toBeInTheDocument();
        expect(screen.getByText('むすこの記録')).toBeInTheDocument();
        // 記録が無い人には空状態が出る
        expect(screen.getByText('まだ記録がありません')).toBeInTheDocument();
    });

    // M-6-4: 報酬購入(type='reward')はゴールドを消費した記録なので "-N G"、
    // クエスト達成は獲得なので "+N G"。以前は購入も一律 "+" 表示だった。
    it('報酬購入は -N G、クエスト達成は +N G で表示する(M-6-4)', () => {
        render(
            <FamilyLog
                chronicle={[
                    log({ userId: 'dad', text: '購入', type: 'reward', gold: 30 }),
                    log({ userId: 'son', text: '達成', type: 'quest', gold: 20 }),
                ]}
                users={users}
            />,
        );
        expect(screen.getByText('-30 G')).toBeInTheDocument();
        expect(screen.getByText('+20 G')).toBeInTheDocument();
    });

    it('ゴールドが0の記録には増減を表示しない', () => {
        render(<FamilyLog chronicle={[log({ userId: 'dad', text: '記録', gold: 0 })]} users={users} />);
        expect(screen.queryByText(/G$/)).not.toBeInTheDocument();
    });

    it('日付ごとにまとめて見出しを出す', () => {
        render(
            <FamilyLog
                chronicle={[
                    log({ userId: 'dad', text: 'A', dateStr: '2026/09/20' }),
                    log({ userId: 'dad', text: 'B', dateStr: '2026/09/21' }),
                    log({ userId: 'dad', text: 'C', dateStr: '2026/09/21' }),
                ]}
                users={users}
            />,
        );
        expect(screen.getByText('2026/09/20')).toBeInTheDocument();
        expect(screen.getByText('2026/09/21')).toBeInTheDocument();
    });

    it('dateStr が無い記録も落とさずプレースホルダの日付へ寄せる', () => {
        render(<FamilyLog chronicle={[log({ userId: 'dad', text: 'A', dateStr: undefined })]} users={users} />);
        expect(screen.getByText('----/--/--')).toBeInTheDocument();
    });

    // #412(F-L2): timestamp が同秒の記録が複数あると key が衝突して描画が壊れていた。
    it('同時刻の記録が複数あってもすべて描画する(#412)', () => {
        render(
            <FamilyLog
                chronicle={[
                    log({ userId: 'dad', text: '同時刻1' }),
                    log({ userId: 'dad', text: '同時刻2' }),
                ]}
                users={users}
            />,
        );
        expect(screen.getByText('同時刻1')).toBeInTheDocument();
        expect(screen.getByText('同時刻2')).toBeInTheDocument();
    });

    it('名前タブで表示する人を切り替えられる', () => {
        render(<FamilyLog chronicle={[]} users={users} initialUserId="dad" />);
        const dadColumn = screen.getByText('とうさん', { selector: 'h3' }).closest('div')!.parentElement!;
        expect(dadColumn.parentElement!.className).toContain('block');

        fireEvent.click(screen.getByRole('button', { name: 'むすこ' }));
        const sonColumn = screen.getByText('むすこ', { selector: 'h3' }).closest('div')!.parentElement!;
        expect(sonColumn.parentElement!.className).toContain('block');
    });

    it('initialUserId を省略すると先頭のユーザーを選ぶ', () => {
        render(<FamilyLog chronicle={[]} users={users} />);
        const dadTab = screen.getByRole('button', { name: 'とうさん' });
        expect(dadTab.className).toContain('bg-purple-600');
    });
});

describe('FamilyLog のスワイプ切り替え', () => {
    it('左スワイプで次の人、右スワイプで前の人', () => {
        render(<FamilyLog chronicle={[]} users={users} initialUserId="son" />);
        swipe(-100);
        expect(screen.getByRole('button', { name: 'かあさん' }).className).toContain('bg-purple-600');
        swipe(100);
        expect(screen.getByRole('button', { name: 'むすこ' }).className).toContain('bg-purple-600');
    });

    it('端では折り返さない(他画面のスワイプ切替と同じ挙動)', () => {
        render(<FamilyLog chronicle={[]} users={users} initialUserId="dad" />);
        swipe(100); // 先頭で右へ
        expect(screen.getByRole('button', { name: 'とうさん' }).className).toContain('bg-purple-600');
    });

    it('しきい値未満の移動では切り替わらない', () => {
        render(<FamilyLog chronicle={[]} users={users} initialUserId="dad" />);
        swipe(-30);
        expect(screen.getByRole('button', { name: 'とうさん' }).className).toContain('bg-purple-600');
    });

    // sm以上では全員を並べるため、本文のテキスト選択などのドラッグで
    // 誤って切り替わらないようスワイプ判定自体を行わない。
    it('タブレット幅以上ではスワイプで切り替えない', () => {
        setViewport(true);
        render(<FamilyLog chronicle={[]} users={users} initialUserId="dad" />);
        swipe(-100);
        expect(screen.getByRole('button', { name: 'とうさん' }).className).toContain('bg-purple-600');
    });
});
