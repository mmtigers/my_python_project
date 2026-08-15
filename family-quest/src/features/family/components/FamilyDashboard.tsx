import React, { useState } from 'react';
import { Sword, ShoppingBag } from 'lucide-react';
import { User, Quest, QuestHistory, Reward, PendingInventory } from '@/types';
import UserStatusCard from './UserStatusCard';
import QuestList from '../../quest/components/QuestList';
import ApprovalList from '../../quest/components/ApprovalList';
import RewardShop from '../../shop/components/RewardShop';
import { useSettings } from '@/context/useSettings';
import { THEME_BORDER_CLASSES, THEME_RING_CLASSES } from '@/context/settingsShared';
import { getQuestLockState } from '../../quest/hooks/useQuestStatus';

// 表示順(パパ・ママ・兄・妹)を固定するための並び替えキー(要件5)。
// 権限判定(quest_users.role)とは別の「画面上の並び順」の関心事のため、ここでのみ
// user_id を直接使う(Family Questの家族構成は固定のため妥当と判断)。
const FAMILY_ORDER = ['dad', 'mom', 'son', 'daughter'];

function sortByFamilyOrder(users: User[]): User[] {
    return [...users].sort((a, b) => {
        const ia = FAMILY_ORDER.indexOf(a.user_id);
        const ib = FAMILY_ORDER.indexOf(b.user_id);
        if (ia === -1 && ib === -1) return 0;
        if (ia === -1) return 1;
        if (ib === -1) return -1;
        return ia - ib;
    });
}

interface FamilyDashboardProps {
    users: User[];
    quests: Quest[];
    completedQuests: QuestHistory[];
    pendingQuests: QuestHistory[];
    rewards: Reward[];
    pendingInventory: PendingInventory[];
    onQuestClick: (user: User, quest: Quest) => void;
    onBuyReward: (user: User, reward: Reward) => void;
    onApprove: (history: QuestHistory) => void;
    onReject: (history: QuestHistory) => void;
    onApproveAll: () => void;
    onAvatarClick: (user: User) => void;
}

// 横画面(Echo Show 15等の常設デバイス)用メインレイアウト。
// パパ・ママ・兄・妹を1行4列で常時表示し、各パネル内でその人のステータスと
// その日のクエスト一覧が完結する(別画面への誘導をしない)。親向けの承認機能は
// 独立画面を持たず、このメイン画面上部に常時統合表示する。
const FamilyDashboard: React.FC<FamilyDashboardProps> = ({
    users, quests, completedQuests, pendingQuests, rewards, pendingInventory,
    onQuestClick, onBuyReward, onApprove, onReject, onApproveAll, onAvatarClick,
}) => {
    const { iconFirstUserIds, userThemeColors } = useSettings();
    const orderedUsers = sortByFamilyOrder(users);
    // 承認バーの記録名義は「親」で固定し、実際に画面をタップしたのがどちらの親かは
    // 区別しない(要件5: 現状も厳密なセキュリティ境界ではないための最もシンプルな方式)。
    const representativeParent = orderedUsers.find(u => u.role === 'role_adult') || orderedUsers[0];

    // 角度⑥: 直前に操作したパネルを枠でハイライトし、常時4人表示でも
    // 「今どこを触っているか」が一目でわかるようにする。
    const [activeUserId, setActiveUserId] = useState<string | null>(null);

    // 今日やることが1件もない人は、パネル自体は残しつつ視覚的な優先度を下げる
    // (角度⑥と隣接する着想: 空パネルに余計な視線誘導をしない)
    const hasNothingToDo = (user: User) => {
        return !quests.some(q => {
            if (q.target && q.target !== 'all') {
                if (q.target.startsWith('role_')) {
                    if (user.role !== q.target) return false;
                } else if (q.target !== user.user_id) {
                    return false;
                }
            }
            const { isLocked, isDone } = getQuestLockState(q, user, completedQuests, pendingQuests);
            return !isLocked && !isDone;
        });
    };

    return (
        <div className="flex flex-col gap-4 animate-in fade-in duration-300">
            {representativeParent && (
                <ApprovalList
                    pendingQuests={pendingQuests}
                    pendingItems={pendingInventory}
                    users={users}
                    currentUser={representativeParent}
                    onApprove={onApprove}
                    onReject={onReject}
                    onApproveAll={onApproveAll}
                />
            )}

            <div className="grid grid-cols-4 gap-3">
                {orderedUsers.map(user => (
                    <FamilyPanel
                        key={user.user_id}
                        user={user}
                        quests={quests}
                        completedQuests={completedQuests}
                        pendingQuests={pendingQuests}
                        rewards={rewards}
                        iconFirst={iconFirstUserIds.includes(user.user_id)}
                        isActive={activeUserId === user.user_id}
                        themeColorKey={userThemeColors[user.user_id]}
                        isIdle={hasNothingToDo(user)}
                        onInteract={() => setActiveUserId(user.user_id)}
                        onQuestClick={(q) => onQuestClick(user, q)}
                        onBuyReward={(r) => onBuyReward(user, r)}
                        onAvatarClick={() => onAvatarClick(user)}
                    />
                ))}
            </div>
        </div>
    );
};

interface FamilyPanelProps {
    user: User;
    quests: Quest[];
    completedQuests: QuestHistory[];
    pendingQuests: QuestHistory[];
    rewards: Reward[];
    iconFirst: boolean;
    isActive: boolean;
    themeColorKey?: keyof typeof THEME_BORDER_CLASSES;
    isIdle: boolean;
    onInteract: () => void;
    onQuestClick: (quest: Quest) => void;
    onBuyReward: (reward: Reward) => void;
    onAvatarClick: () => void;
}

const FamilyPanel: React.FC<FamilyPanelProps> = ({
    user, quests, completedQuests, pendingQuests, rewards, iconFirst, isActive, themeColorKey, isIdle,
    onInteract, onQuestClick, onBuyReward, onAvatarClick,
}) => {
    const [tab, setTab] = useState<'quest' | 'shop'>('quest');

    const borderClass = isActive
        ? (themeColorKey ? THEME_BORDER_CLASSES[themeColorKey] : 'border-yellow-400')
        : 'border-gray-700';
    const ringClass = isActive
        ? `ring-2 ${themeColorKey ? THEME_RING_CLASSES[themeColorKey] : 'ring-yellow-400/50'}`
        : '';

    return (
        <div
            onClickCapture={onInteract}
            className={`flex flex-col bg-black/30 border-2 rounded-xl overflow-hidden min-w-0 transition-all duration-300 ${borderClass} ${ringClass} ${isIdle ? 'opacity-70' : ''}`}
        >
            <div className="p-2 border-b border-gray-700">
                <UserStatusCard user={user} onAvatarClick={onAvatarClick} />
            </div>

            {/* タブ切替: Echo Show 15でのタッチ操作を想定し、タップ領域を大きめに確保 */}
            <div className="flex gap-1 p-2 bg-black/40">
                <button
                    onClick={() => setTab('quest')}
                    className={`flex-1 min-h-[44px] py-2 text-xs font-bold rounded-lg flex flex-col items-center justify-center transition-all ${tab === 'quest' ? 'bg-blue-600 text-white shadow-md' : 'text-gray-400 bg-gray-900/60'
                        }`}
                >
                    <Sword size={18} className="mb-0.5" /> クエスト
                </button>
                <button
                    onClick={() => setTab('shop')}
                    className={`flex-1 min-h-[44px] py-2 text-xs font-bold rounded-lg flex flex-col items-center justify-center transition-all ${tab === 'shop' ? 'bg-orange-500 text-white shadow-md' : 'text-gray-400 bg-gray-900/60'
                        }`}
                >
                    <ShoppingBag size={18} className="mb-0.5" /> ごほうび
                </button>
            </div>

            {/* パネルごとに独立スクロール(要件5) */}
            <div className="p-2 overflow-y-auto max-h-[60vh]">
                {tab === 'quest' ? (
                    <QuestList
                        quests={quests}
                        completedQuests={completedQuests}
                        pendingQuests={pendingQuests}
                        currentUser={user}
                        onQuestClick={onQuestClick}
                        panelMode
                        iconFirst={iconFirst}
                    />
                ) : (
                    <RewardShop
                        rewards={rewards}
                        currentUser={user}
                        onBuy={onBuyReward}
                    />
                )}
            </div>
        </div>
    );
};

export default FamilyDashboard;
