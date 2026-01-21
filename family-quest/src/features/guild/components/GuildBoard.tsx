// features/guild/components/GuildBoard.tsx
import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

import { fetchBounties, createBounty, acceptBounty, completeBounty, approveBounty } from '../../../lib/apiClient';
import { Bounty } from '../../../types';

import { Card } from '../../../components/ui/Card';
import { Button } from '../../../components/ui/Button';
import { Modal } from '../../../components/ui/Modal';
import { useSound } from '../../../hooks/useSound';

// 型定義
interface CreateBountyForm {
    title: string;
    description: string;
    reward_gold: number;
    target_type: 'ALL' | 'ADULTS' | 'CHILDREN';
}

interface GuildBoardProps {
    userId: string;
}

export const GuildBoard: React.FC<GuildBoardProps> = ({ userId }) => {
    const queryClient = useQueryClient();

    // ★修正1: play関数をオブジェクトから取り出す (Destructuring)
    const { play } = useSound();

    const [activeTab, setActiveTab] = useState<'OPEN' | 'MINE'>('OPEN');
    const [isModalOpen, setIsModalOpen] = useState(false);

    // フォーム状態
    const [form, setForm] = useState<CreateBountyForm>({
        title: '', description: '', reward_gold: 100, target_type: 'ALL'
    });

    // データ取得
    const { data: bounties = [], isLoading } = useQuery({
        queryKey: ['bounties', userId],
        queryFn: () => fetchBounties(userId),
        enabled: !!userId,
        refetchInterval: 5000,
    });

    // 受注処理
    const acceptMutation = useMutation({
        mutationFn: (bountyId: number) => acceptBounty(bountyId, userId),
        onSuccess: () => {
            // ★修正2: 'decision' -> 'submit' (存在するキーに変更)
            play('submit');
            queryClient.invalidateQueries({ queryKey: ['bounties'] });
            alert("クエストを受注しました！");
        },
        onError: (err: Error) => alert(err.message),
    });

    // ★追加: 完了報告処理
    const completeMutation = useMutation({
        mutationFn: (bountyId: number) => completeBounty(bountyId, userId),
        onSuccess: () => {
            play('submit');
            queryClient.invalidateQueries({ queryKey: ['bounties'] });
            alert("完了報告しました！承認を待ちましょう。");
        },
        onError: (err: Error) => alert(err.message),
    });

    // ★追加: 承認処理
    const approveMutation = useMutation({
        mutationFn: (bountyId: number) => approveBounty(bountyId, userId),
        onSuccess: () => {
            play('medal'); // ファンファーレ音
            queryClient.invalidateQueries({ queryKey: ['bounties'] });
            queryClient.invalidateQueries({ queryKey: ['gameData'] }); // 所持金を更新
            alert("承認しました！報酬が支払われました。");
        },
        onError: (err: Error) => alert(err.message),
    });

    // 作成処理
    const createMutation = useMutation({
        mutationFn: (data: CreateBountyForm) => createBounty({ ...data, created_by: userId }),
        onSuccess: () => {
            // ★修正3: 'save' -> 'submit'
            play('submit');
            setIsModalOpen(false);
            setForm({ title: '', description: '', reward_gold: 100, target_type: 'ALL' });
            queryClient.invalidateQueries({ queryKey: ['bounties'] });
        }
    });

    // 表示用フィルタリング
    const displayBounties = bounties.filter((b: Bounty) => {
        if (activeTab === 'OPEN') {
            return b.status === 'OPEN';
        } else {
            return b.is_mine || b.is_assigned_to_me;
        }
    });

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        createMutation.mutate(form);
    };

    if (isLoading) return <div className="text-white text-center p-4">読み込み中...</div>;

    return (
        <div className="p-2 max-w-4xl mx-auto space-y-4 pb-20">
            {/* ヘッダーエリア */}
            <div className="flex justify-between items-center mb-2">
                <h2 className="text-xl text-yellow-300 drop-shadow-md font-bold">🛡️ ギルド依頼板</h2>
                <Button variant="primary" onClick={() => setIsModalOpen(true)}>
                    ＋ 依頼を出す
                </Button>
            </div>

            {/* タブ切り替え */}
            <div className="flex space-x-2 border-b-2 border-white/20 pb-2">
                <button
                    onClick={() => { play('tap'); setActiveTab('OPEN'); }} // playSound -> play
                    className={`px-4 py-1 rounded-t-lg transition-colors ${activeTab === 'OPEN' ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400'}`}
                >
                    📜 募集中
                </button>
                <button
                    onClick={() => { play('tap'); setActiveTab('MINE'); }} // playSound -> play
                    className={`px-4 py-1 rounded-t-lg transition-colors ${activeTab === 'MINE' ? 'bg-green-700 text-white' : 'bg-gray-800 text-gray-400'}`}
                >
                    🎒 受注・作成済み
                </button>
            </div>

            {/* リスト表示エリア */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {displayBounties.length === 0 && (
                    <div className="col-span-full text-center text-gray-400 py-8">
                        依頼はありません
                    </div>
                )}

                {displayBounties.map((b: Bounty) => (
                    <Card key={b.id} className="relative border-2 border-white bg-gray-900/90">
                        <div className="absolute top-0 right-0 px-2 py-1 text-xs font-bold text-white bg-black/50 border-l border-b border-white">
                            {b.status === 'OPEN' && <span className="text-yellow-400">募集中</span>}
                            {b.status === 'TAKEN' && <span className="text-blue-400">受注中</span>}
                            {b.status === 'PENDING_APPROVAL' && <span className="text-green-400">承認待ち</span>}
                            {b.status === 'COMPLETED' && <span className="text-gray-400">完了</span>}
                        </div>

                        <div className="p-1">
                            <h3 className="text-lg font-bold text-white mb-1">
                                {b.target_type === 'CHILDREN' && '👶 '}
                                {b.target_type === 'ADULTS' && '🍷 '}
                                {b.title}
                            </h3>

                            <div className="text-sm text-gray-300 mb-2 min-h-[40px]">
                                {b.description || '詳細なし'}
                            </div>

                            <div className="flex justify-between items-end border-t border-gray-600 pt-2">
                                <div className="text-yellow-300 font-mono text-lg">
                                    💰 {b.reward_gold} G
                                </div>

                                {/* ▼▼▼ アクションボタンエリア ▼▼▼ */}
                                <div>
                                    {/* 受注ボタン */}
                                    {b.status === 'OPEN' && b.can_accept && (
                                        <Button
                                            variant="primary"
                                            size="sm"
                                            onClick={() => acceptMutation.mutate(b.id)}
                                            disabled={acceptMutation.isPending}
                                        >
                                            受注する
                                        </Button>
                                    )}
                                    {b.is_mine && b.status === 'OPEN' && (
                                        <span className="text-xs text-gray-500">募集中...</span>
                                    )}

                                    {/* 完了報告ボタン (自分が受注者 & 受注中) */}
                                    {b.status === 'TAKEN' && b.is_assigned_to_me && (
                                        <Button
                                            variant="success"
                                            size="sm"
                                            onClick={() => completeMutation.mutate(b.id)}
                                            disabled={completeMutation.isPending}
                                        >
                                            完了報告
                                        </Button>
                                    )}

                                    {/* 承認ボタン (自分が依頼主 & 承認待ち) */}
                                    {b.status === 'PENDING_APPROVAL' && b.is_mine && (
                                        <div className="flex flex-col items-end">
                                            <span className="text-xs text-green-400 mb-1 animate-pulse">報告が届いています！</span>
                                            <Button
                                                variant="warning"
                                                size="sm"
                                                onClick={() => approveMutation.mutate(b.id)}
                                                disabled={approveMutation.isPending}
                                            >
                                                承認＆報酬
                                            </Button>
                                        </div>
                                    )}

                                    {/* 承認待ち（相手側） */}
                                    {b.status === 'PENDING_APPROVAL' && !b.is_mine && (
                                        <span className="text-xs text-gray-400">承認待ち...</span>
                                    )}

                                    {/* 完了済み */}
                                    {b.status === 'COMPLETED' && (
                                        <span className="text-xs text-yellow-500 font-bold">解決済み</span>
                                    )}
                                </div>
                                {/* ▲▲▲ エリア終了 ▲▲▲ */}
                            </div>

                            <div className="mt-2 text-xs text-gray-500 flex justify-between">
                                <span>依頼: {b.created_by}</span>
                                {b.assignee_id && <span>担当: {b.assignee_id}</span>}
                            </div>
                        </div>
                    </Card>
                ))}
            </div>

            <Modal isOpen={isModalOpen} onClose={() => setIsModalOpen(false)} title="新規依頼を作成">
                <form onSubmit={handleSubmit} className="space-y-4 text-white">
                    <div>
                        <label className="block text-sm text-gray-300 mb-1">依頼タイトル</label>
                        <input
                            required
                            className="w-full bg-black border border-gray-500 rounded p-2 focus:border-yellow-400 outline-none"
                            placeholder="例: お風呂掃除、牛乳買ってきて"
                            value={form.title}
                            onChange={e => setForm({ ...form, title: e.target.value })}
                        />
                    </div>
                    <div>
                        <label className="block text-sm text-gray-300 mb-1">詳細 (オプション)</label>
                        <textarea
                            className="w-full bg-black border border-gray-500 rounded p-2 focus:border-yellow-400 outline-none h-20"
                            placeholder="細かい指示があればここへ"
                            value={form.description}
                            onChange={e => setForm({ ...form, description: e.target.value })}
                        />
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                        <div>
                            <label className="block text-sm text-gray-300 mb-1">報酬 (G)</label>
                            <input
                                type="number"
                                min="0"
                                step="10"
                                className="w-full bg-black border border-gray-500 rounded p-2"
                                value={form.reward_gold}
                                onChange={e => setForm({ ...form, reward_gold: parseInt(e.target.value) })}
                            />
                        </div>
                        <div>
                            <label className="block text-sm text-gray-300 mb-1">誰に頼む？</label>
                            <select
                                className="w-full bg-black border border-gray-500 rounded p-2"
                                value={form.target_type}
                                onChange={e => setForm({ ...form, target_type: e.target.value as any })}
                            >
                                <option value="ALL">全員</option>
                                <option value="CHILDREN">子供たち</option>
                                <option value="ADULTS">大人（パパ・ママ）</option>
                            </select>
                        </div>
                    </div>
                    <div className="flex justify-end space-x-2 pt-4">
                        <Button variant="secondary" onClick={() => setIsModalOpen(false)} type="button">
                            やめる
                        </Button>
                        <Button variant="primary" type="submit" disabled={createMutation.isPending}>
                            掲示板に貼る
                        </Button>
                    </div>
                </form>
            </Modal>
        </div>
    );
};