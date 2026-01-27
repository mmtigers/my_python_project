// features/guild/components/GuildBoard.tsx
import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import confetti from 'canvas-confetti';
import { Trash2, XCircle, ShieldAlert } from 'lucide-react';

import {
    fetchBounties, createBounty, acceptBounty, completeBounty, approveBounty,
    deleteBounty, resignBounty // ★追加
} from '../../../lib/apiClient';
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

    // --- Actions ---

    const acceptMutation = useMutation({
        mutationFn: (bountyId: number) => acceptBounty(bountyId, userId),
        onSuccess: () => {
            play('submit');
            queryClient.invalidateQueries({ queryKey: ['bounties'] });
            // 小さな演出
            confetti({ particleCount: 50, spread: 60, origin: { y: 0.7 } });
        },
        onError: (err: Error) => alert(err.message),
    });

    const completeMutation = useMutation({
        mutationFn: (bountyId: number) => completeBounty(bountyId, userId),
        onSuccess: () => {
            play('submit');
            queryClient.invalidateQueries({ queryKey: ['bounties'] });
        },
        onError: (err: Error) => alert(err.message),
    });

    const approveMutation = useMutation({
        mutationFn: (bountyId: number) => approveBounty(bountyId, userId),
        onSuccess: () => {
            play('medal');
            queryClient.invalidateQueries({ queryKey: ['bounties'] });
            queryClient.invalidateQueries({ queryKey: ['gameData'] });

            // ★追加: 豪華な演出 (ゴールドカラーの紙吹雪)
            const duration = 2000;
            const end = Date.now() + duration;

            (function frame() {
                confetti({
                    particleCount: 5,
                    angle: 60,
                    spread: 55,
                    origin: { x: 0 },
                    colors: ['#FFD700', '#FFA500'] // Gold colors
                });
                confetti({
                    particleCount: 5,
                    angle: 120,
                    spread: 55,
                    origin: { x: 1 },
                    colors: ['#FFD700', '#FFA500']
                });

                if (Date.now() < end) {
                    requestAnimationFrame(frame);
                }
            }());
        },
        onError: (err: Error) => alert(err.message),
    });

    const createMutation = useMutation({
        mutationFn: (data: CreateBountyForm) => createBounty({ ...data, created_by: userId }),
        onSuccess: () => {
            play('submit');
            setIsModalOpen(false);
            setForm({ title: '', description: '', reward_gold: 100, target_type: 'ALL' });
            queryClient.invalidateQueries({ queryKey: ['bounties'] });
        }
    });

    // ★追加: 削除処理
    const deleteMutation = useMutation({
        mutationFn: (bountyId: number) => deleteBounty(bountyId, userId),
        onSuccess: () => {
            play('cancel');
            queryClient.invalidateQueries({ queryKey: ['bounties'] });
        }
    });

    // ★追加: 辞退処理
    const resignMutation = useMutation({
        mutationFn: (bountyId: number) => resignBounty(bountyId, userId),
        onSuccess: () => {
            play('cancel');
            queryClient.invalidateQueries({ queryKey: ['bounties'] });
        }
    });

    // --- Helpers ---

    const handleDelete = (bountyId: number) => {
        if (confirm("この依頼を取り下げますか？")) {
            deleteMutation.mutate(bountyId);
        }
    };

    const handleResign = (bountyId: number) => {
        if (confirm("受注を辞退しますか？\n(ペナルティはありません)")) {
            resignMutation.mutate(bountyId);
        }
    };

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        createMutation.mutate(form);
    };

    // 表示用フィルタリング
    const displayBounties = bounties.filter((b: Bounty) => {
        if (activeTab === 'OPEN') {
            return b.status === 'OPEN';
        } else {
            return b.is_mine || b.is_assigned_to_me;
        }
    });



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

                {/* ★Empty State: 世界観の強化 */}
                {displayBounties.length === 0 && (
                    <div className="col-span-full py-12 px-4 text-center border-2 border-dashed border-gray-700 rounded-lg bg-gray-900/50">
                        <div className="text-6xl mb-4 opacity-50">🍃</div>
                        <h3 className="text-gray-400 text-lg font-bold mb-2">
                            {activeTab === 'OPEN' ? "現在の依頼はありません" : "履歴はありません"}
                        </h3>
                        <p className="text-gray-500 text-sm">
                            {activeTab === 'OPEN'
                                ? "ギルドは平和そのものです。\n困りごとがあれば「依頼を出す」から作成しましょう。"
                                : "まだ冒険は始まっていません。"}
                        </p>
                    </div>
                )}

                {displayBounties.map((b: Bounty) => (
                    <Card key={b.id} className={`relative border-2 bg-gray-900/90 transition-all duration-300 ${b.status === 'COMPLETED' ? 'border-gray-600 opacity-70 grayscale-[0.5]' : 'border-white hover:scale-[1.01]'
                        }`}>
                        {/* ステータスバッジ */}
                        <div className="absolute top-0 right-0 px-2 py-1 text-xs font-bold text-white bg-black/80 border-l border-b border-white z-10">
                            {b.status === 'OPEN' && <span className="text-yellow-400 animate-pulse">募集中</span>}
                            {b.status === 'TAKEN' && <span className="text-blue-400">受注中</span>}
                            {b.status === 'PENDING_APPROVAL' && <span className="text-green-400">承認待ち</span>}
                            {b.status === 'COMPLETED' && <span className="text-gray-400">完了</span>}
                        </div>

                        <div className="p-2 flex flex-col h-full">
                            <h3 className="text-lg font-bold text-white mb-1 pr-16 line-clamp-1">
                                {b.target_type === 'CHILDREN' && '👶 '}
                                {b.target_type === 'ADULTS' && '🍷 '}
                                {b.title}
                            </h3>

                            <div className="text-sm text-gray-300 mb-3 min-h-[40px] whitespace-pre-wrap bg-gray-800/50 p-2 rounded">
                                {b.description || '詳細なし'}
                            </div>

                            <div className="mt-auto pt-2 border-t border-gray-700">
                                <div className="flex justify-between items-center mb-2">
                                    <div className="text-yellow-300 font-mono text-xl font-bold drop-shadow-md">
                                        💰 {b.reward_gold} G
                                    </div>
                                    <div className="text-xs text-gray-500 text-right">
                                        依頼: {b.created_by}<br />
                                        {b.assignee_id && `担当: ${b.assignee_id}`}
                                    </div>
                                </div>

                                {/* ▼▼▼ アクションボタンエリア ▼▼▼ */}
                                <div className="flex justify-end gap-2">

                                    {/* 1. 作成者による取り下げ (OPEN時) */}
                                    {b.is_mine && b.status === 'OPEN' && (
                                        <Button
                                            variant="secondary"
                                            size="sm"
                                            className="text-red-300 border-red-900 bg-red-900/20 hover:bg-red-900/50"
                                            onClick={() => handleDelete(b.id)}
                                        >
                                            <Trash2 size={14} className="mr-1" /> 取り下げ
                                        </Button>
                                    )}

                                    {/* 2. 他者による受注 (OPEN時) */}
                                    {b.status === 'OPEN' && b.can_accept && (
                                        <Button
                                            variant="primary"
                                            size="sm"
                                            onClick={() => acceptMutation.mutate(b.id)}
                                            disabled={acceptMutation.isPending}
                                            className="w-full"
                                        >
                                            この依頼を受ける！
                                        </Button>
                                    )}

                                    {/* 3. 受注者による辞退 (TAKEN時) */}
                                    {b.status === 'TAKEN' && b.is_assigned_to_me && (
                                        <Button
                                            variant="secondary"
                                            size="sm"
                                            className="text-gray-400"
                                            onClick={() => handleResign(b.id)}
                                        >
                                            <XCircle size={14} className="mr-1" /> 辞退
                                        </Button>
                                    )}

                                    {/* 4. 受注者による完了報告 (TAKEN時) */}
                                    {b.status === 'TAKEN' && b.is_assigned_to_me && (
                                        <Button
                                            variant="success"
                                            size="sm"
                                            onClick={() => completeMutation.mutate(b.id)}
                                            disabled={completeMutation.isPending}
                                            className="flex-1"
                                        >
                                            報告する
                                        </Button>
                                    )}

                                    {/* 5. 依頼主による承認 (PENDING_APPROVAL時) */}
                                    {b.status === 'PENDING_APPROVAL' && b.is_mine && (
                                        <div className="flex flex-col w-full">
                                            <span className="text-xs text-green-400 mb-1 animate-pulse text-center">報告が届いています！</span>
                                            <Button
                                                variant="warning" // Button.tsxに追加したのでOK
                                                size="sm"
                                                onClick={() => approveMutation.mutate(b.id)}
                                                disabled={approveMutation.isPending}
                                                className="w-full"
                                            >
                                                承認して報酬を払う
                                            </Button>
                                        </div>
                                    )}

                                    {/* 待機メッセージ */}
                                    {b.status === 'PENDING_APPROVAL' && !b.is_mine && (
                                        <span className="text-xs text-gray-400 flex items-center">
                                            <ShieldAlert size={12} className="mr-1" /> 承認待ち...
                                        </span>
                                    )}
                                </div>
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