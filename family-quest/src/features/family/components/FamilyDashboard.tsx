import React, { useState } from 'react';
import { Sword, ShoppingBag, Package } from 'lucide-react';
import { User, Quest, QuestHistory, Reward } from '@/types';
import UserStatusCard from './UserStatusCard';
import QuestList from '../../quest/components/QuestList';
import ApprovalList from '../../quest/components/ApprovalList';
import RewardShop from '../../shop/components/RewardShop';
import { InventoryList } from '../../shop/components/InventoryList';
import { useSettings } from '@/context/useSettings';
import { THEME_BORDER_CLASSES, THEME_RING_CLASSES } from '@/context/settingsShared';
import { getQuestLockState } from '../../quest/hooks/useQuestStatus';
import { isQuestVisibleToUser } from '@/lib/questTargeting';
import { useRoutineData } from '@/hooks/useRoutineData';
import RoutineFlow, { RoutineFreeTimeBanner } from '../../routine/components/RoutineFlow';
import { selectRoutineFlow } from '@/lib/routineDataSchema';
import { useSound } from '@/hooks/useSound';
import { useToast } from '@/context/useToast';
import { useQuestActivity } from '../../quest/context/useQuestActivity';

interface FamilyDashboardProps {
    users: User[];
    quests: Quest[];
    completedQuests: QuestHistory[];
    pendingQuests: QuestHistory[];
    rewards: Reward[];
    onQuestClick: (user: User, quest: Quest) => void;
    onBuyReward: (user: User, reward: Reward) => void;
    onApprove: (history: QuestHistory) => void;
    onReject: (history: QuestHistory) => void;
    onApproveAll: () => void;
    // #659: completedSignal / processingQuestKeys / busyHistoryIds は
    // QuestActivityContext から読む。以前はここと FamilyPanelProps に素通しの
    // props として並んでおり、中継する2つのコンポーネントは値を使わないのに
    // 型と引数だけを持たされていた。
    isApprovingAll?: boolean;
    onAvatarClick: (user: User) => void;
}

// 横画面(Echo Show 15等の常設デバイス)用メインレイアウト。
// パパ・ママ・兄・妹を1行4列で常時表示し、各パネル内でその人のステータスと
// その日のクエスト一覧が完結する(別画面への誘導をしない)。親向けの承認機能は
// 独立画面を持たず、このメイン画面上部に常時統合表示する。
const FamilyDashboard: React.FC<FamilyDashboardProps> = ({
    users, quests, completedQuests, pendingQuests, rewards,
    onQuestClick, onBuyReward, onApprove, onReject, onApproveAll,
    isApprovingAll, onAvatarClick,
}) => {
    const { iconFirstUserIds, userThemeColors } = useSettings();
    const { busyHistoryIds } = useQuestActivity();
    // #412(品質): 以前はここで FAMILY_ORDER=['dad','mom','son','daughter'] という
    // ハードコードされた並び順に再ソートしていたが、サーバー側(quest_service.pyの
    // GameSystem.get_all_view_data)が既に quest_data.USERS の宣言順(同じ dad→mom→
    // son→daughter)を正としてソート済みの users を返すため、クライアント側での
    // 再ソートは不要かつ重複していた。users をそのまま表示順として使う。
    const orderedUsers = users;
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
            // #412(品質): 判定ロジックは lib/questTargeting.ts に集約(QuestList.tsx と共通)。
            if (!isQuestVisibleToUser(q, user)) return false;
            const { isLocked, isDone } = getQuestLockState(q, user, completedQuests, pendingQuests);
            return !isLocked && !isDone;
        });
    };

    return (
        <div className="flex flex-col gap-4 animate-in fade-in duration-300">
            {representativeParent && (
                <ApprovalList
                    pendingQuests={pendingQuests}
                    users={users}
                    onApprove={onApprove}
                    onReject={onReject}
                    onApproveAll={onApproveAll}
                    busyHistoryIds={busyHistoryIds}
                    isApprovingAll={isApprovingAll}
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
    const [tab, setTab] = useState<'quest' | 'shop' | 'inventory'>('quest');
    // #659: 表示に使うだけの横断的な状態は Context から直接読む(素通しの props を廃止)。
    const { completedSignal, processingQuestKeys } = useQuestActivity();
    const { play } = useSound();
    const { showToast } = useToast();

    // 「きょうのすごろく」: パネルごとに自分のペースで進む(全員同じフロー定義を
    // 個別に進行する想定、CLAUDE.md参照)。誘導中はクエスト一覧より優先表示する。
    // #(コードレビューで発覚): 完了報告失敗時のエラートースト表示(旧handleRoutineStepComplete)
    // がApp.tsx側と一字一句重複していたため、useRoutineData自体のonErrorへ集約した。
    const { flows: routineFlows, completeStep: completeRoutineStep, isCompleting: isCompletingRoutine } = useRoutineData(
        user.user_id,
        (info) => {
            play('levelUp');
            showToast({ title: 'LEVEL UP!', text: `${user.name}は Lv.${info.newLevel} になった！`, icon: '⚡' });
        },
        (detail) => {
            showToast({ title: 'エラー', text: detail || '通信状態を確認し、もう一度お試しください', icon: '⚠️' });
            play('cancel');
        },
        // 大人用フローに寄せたステップ(ママの「夕食を作る」等)の即時報酬。
        (reward) => {
            play('clear');
            showToast({ title: 'クリア！', text: `${user.name}は ${reward.gold} G を手に入れた！`, icon: '💰' });
        }
    );
    const { activeKey: activeRoutineKey, freeTimeKey: freeTimeRoutineKey } = selectRoutineFlow(routineFlows);

    // ★バグ修正: 以前はテーマカラーを isActive(直前に操作したパネル)の時だけ適用していたため、
    // 設定画面で色を選んでも、操作するまでメイン画面(横画面)に何も反映されなかった。
    // パネルの縁取りは常にそのユーザーのテーマカラーを表示し、リング(強調枠)だけを
    // 「直前に操作した」ことの一時的なハイライトとして使う。
    const borderClass = themeColorKey
        ? THEME_BORDER_CLASSES[themeColorKey]
        : (isActive ? 'border-yellow-400' : 'border-gray-700');
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

            {/* タブ切替: Echo Show 15でのタッチ操作を想定し、タップ領域を大きめに確保。
                ★バグ修正: ごほうび画面へのもちもの統合をやめ、クエスト/ごほうび/もちものの3タブに戻す。
                テキストは不要のためアイコンのみ表示する(aria-labelで読み上げは維持) */}
            <div className="flex gap-1 p-2 bg-black/40">
                <button
                    onClick={() => setTab('quest')}
                    aria-label="クエスト"
                    className={`flex-1 min-h-[44px] py-2 rounded-lg flex items-center justify-center transition-all ${tab === 'quest' ? 'bg-blue-600 text-white shadow-md' : 'text-gray-400 bg-gray-900/60'
                        }`}
                >
                    <Sword size={20} />
                </button>
                <button
                    onClick={() => setTab('shop')}
                    aria-label="ごほうび"
                    className={`flex-1 min-h-[44px] py-2 rounded-lg flex items-center justify-center transition-all ${tab === 'shop' ? 'bg-orange-500 text-white shadow-md' : 'text-gray-400 bg-gray-900/60'
                        }`}
                >
                    <ShoppingBag size={20} />
                </button>
                <button
                    onClick={() => setTab('inventory')}
                    aria-label="もちもの"
                    className={`flex-1 min-h-[44px] py-2 rounded-lg flex items-center justify-center transition-all ${tab === 'inventory' ? 'bg-green-600 text-white shadow-md' : 'text-gray-400 bg-gray-900/60'
                        }`}
                >
                    <Package size={20} />
                </button>
            </div>

            {/* パネルごとに独立スクロール(要件5) */}
            <div className="p-2 overflow-y-auto max-h-[60vh]">
                {tab === 'quest' && activeRoutineKey && routineFlows && (
                    <RoutineFlow
                        flowKey={activeRoutineKey}
                        flow={routineFlows[activeRoutineKey]}
                        onCompleteStep={(stepKey) => completeRoutineStep(activeRoutineKey, stepKey)}
                        isCompleting={isCompletingRoutine}
                        compact
                    />
                )}

                {tab === 'quest' && !activeRoutineKey && (
                    <>
                        {freeTimeRoutineKey && routineFlows && (
                            <div className="mb-2">
                                <RoutineFreeTimeBanner flowKey={freeTimeRoutineKey} flow={routineFlows[freeTimeRoutineKey]} />
                            </div>
                        )}
                        <QuestList
                            quests={quests}
                            completedQuests={completedQuests}
                            pendingQuests={pendingQuests}
                            currentUser={user}
                            onQuestClick={onQuestClick}
                            completedSignal={completedSignal}
                            processingQuestKeys={processingQuestKeys}
                            panelMode
                            iconFirst={iconFirst}
                        />
                    </>
                )}
                {tab === 'shop' && (
                    <RewardShop
                        rewards={rewards}
                        currentUser={user}
                        onBuy={onBuyReward}
                    />
                )}
                {tab === 'inventory' && (
                    <InventoryList userId={user.user_id} panelMode />
                )}
            </div>
        </div>
    );
};

export default FamilyDashboard;
