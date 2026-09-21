import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import RewardList from './RewardList';
import { Reward, User } from '@/types';

const adult: User = { user_id: 'dad', name: 'とうさん', level: 1, exp: 0, gold: 500, role: 'role_adult' };
const child: User = { user_id: 'son', name: 'むすこ', level: 1, exp: 0, gold: 500, role: 'role_child' };

const reward = (over: Partial<Reward> & { title: string }): Reward => ({
    cost_gold: 100,
    ...over,
});

function renderList(rewards: Reward[], user: User, userGold = 500) {
    const onBuy = vi.fn();
    render(<RewardList rewards={rewards} userGold={userGold} onBuy={onBuy} currentUser={user} />);
    return { onBuy };
}

afterEach(cleanup);

describe('RewardList の表示対象の絞り込み', () => {
    it('target 未指定は全員に出す', () => {
        renderList([reward({ title: 'アイス' })], child);
        expect(screen.getByText('アイス')).toBeInTheDocument();
    });

    it('all は全員に出す', () => {
        renderList([reward({ title: 'アイス', target: 'all' })], adult);
        expect(screen.getByText('アイス')).toBeInTheDocument();
    });

    it('children は子どもにだけ出す', () => {
        renderList([reward({ title: 'おかし', target: 'children' })], child);
        expect(screen.getByText('おかし')).toBeInTheDocument();
        cleanup();
        renderList([reward({ title: 'おかし', target: 'children' })], adult);
        expect(screen.queryByText('おかし')).not.toBeInTheDocument();
    });

    it('adults は大人にだけ出す', () => {
        renderList([reward({ title: 'ビール', target: 'adults' })], adult);
        expect(screen.getByText('ビール')).toBeInTheDocument();
        cleanup();
        renderList([reward({ title: 'ビール', target: 'adults' })], child);
        expect(screen.queryByText('ビール')).not.toBeInTheDocument();
    });

    it('user_id 宛ては本人にだけ出す', () => {
        renderList([reward({ title: 'むすこ専用', target: 'son' })], child);
        expect(screen.getByText('むすこ専用')).toBeInTheDocument();
        cleanup();
        renderList([reward({ title: 'むすこ専用', target: 'son' })], adult);
        expect(screen.queryByText('むすこ専用')).not.toBeInTheDocument();
    });

    // #239: 未知の target 値はどの分岐にも一致せず無条件 true になり、対象外の家族
    // 全員に表示されていた。deny-by-default に倒すことを固定する。
    it('未知の target は誰にも出さない(deny-by-default / #239)', () => {
        renderList([reward({ title: '謎の商品', target: 'unknown_group' })], child);
        expect(screen.queryByText('謎の商品')).not.toBeInTheDocument();
        cleanup();
        renderList([reward({ title: '謎の商品', target: 'unknown_group' })], adult);
        expect(screen.queryByText('謎の商品')).not.toBeInTheDocument();
    });

    it('対象が1件も無ければ入荷待ちの文言を出す', () => {
        renderList([reward({ title: 'ビール', target: 'adults' })], child);
        expect(screen.getByText('商品が入荷待ちです...')).toBeInTheDocument();
    });
});

describe('RewardList の並びと購入', () => {
    it('安い順に並べる', () => {
        renderList(
            [
                reward({ title: '高い', cost_gold: 300 }),
                reward({ title: '安い', cost_gold: 50 }),
                reward({ title: '中くらい', cost_gold: 150 }),
            ],
            child,
        );
        const titles = screen.getAllByText(/高い|安い|中くらい/).map(e => e.textContent);
        expect(titles).toEqual(['安い', '中くらい', '高い']);
    });

    it('買えるものはクリックで onBuy が呼ばれる', () => {
        const { onBuy } = renderList([reward({ title: 'アイス', cost_gold: 100 })], child, 500);
        fireEvent.click(screen.getByText('アイス'));
        expect(onBuy).toHaveBeenCalledTimes(1);
        expect(onBuy.mock.calls[0][0].title).toBe('アイス');
    });

    it('ゴールドが足りなければクリックしても買えない', () => {
        const { onBuy } = renderList([reward({ title: '高級品', cost_gold: 1000 })], child, 500);
        fireEvent.click(screen.getByText('高級品'));
        expect(onBuy).not.toHaveBeenCalled();
    });

    // #660: 買えない商品も onClick を持つため role="button" のままだが、
    // 支援技術に「押せない」ことが伝わるよう aria-disabled / tabIndex を出し分ける。
    it('買えない商品は aria-disabled で、フォーカスも受け取らない(#660)', () => {
        renderList(
            [reward({ title: '買える', cost_gold: 10 }), reward({ title: '買えない', cost_gold: 9999 })],
            child,
            500,
        );
        const affordable = screen.getByText('買える').closest('[role="button"]')!;
        const tooExpensive = screen.getByText('買えない').closest('[role="button"]')!;
        expect(affordable).toHaveAttribute('aria-disabled', 'false');
        expect(affordable).toHaveAttribute('tabIndex', '0');
        expect(tooExpensive).toHaveAttribute('aria-disabled', 'true');
        expect(tooExpensive).toHaveAttribute('tabIndex', '-1');
    });

    it('ちょうど買える額なら買える(境界)', () => {
        const { onBuy } = renderList([reward({ title: 'ぴったり', cost_gold: 500 })], child, 500);
        fireEvent.click(screen.getByText('ぴったり'));
        expect(onBuy).toHaveBeenCalledTimes(1);
    });

    it('説明・カテゴリが無ければ General を表示する', () => {
        renderList([reward({ title: 'アイス' })], child);
        expect(screen.getByText('General')).toBeInTheDocument();
        cleanup();
        renderList([reward({ title: 'アイス', category: 'おやつ' })], child);
        expect(screen.getByText('おやつ')).toBeInTheDocument();
        cleanup();
        renderList([reward({ title: 'アイス', category: 'おやつ', description: '説明文' })], child);
        expect(screen.getByText('説明文')).toBeInTheDocument();
    });

    it('アイコンが無ければ既定の絵文字を出す', () => {
        renderList([reward({ title: 'アイス' })], child);
        expect(screen.getByText('🎁')).toBeInTheDocument();
    });
});
