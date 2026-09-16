// family-quest/src/features/routine/components/RoutineFlow.tsx
//
// 「きょうのすごろく」— 平日の朝/夕方の生活動線を、縦一列のすごろく/路線図として
// 常時全ステップ表示するUI(療育目的: 現在地と見通しを常に見せる)。
// 自由時間の終了(チェックポイント)だけはサーバー側で時刻ベースに強制通過させる
// (services/routine_service.py)ため、このコンポーネントには手動で進める手段を
// 持たせず、現在のステップの完了報告のみを行う。
import React from 'react';
import { Bell, Check, Clock, Coins, LucideIcon } from 'lucide-react';
import {
    Droplet, UtensilsCrossed, Shirt, Sparkles, Star,
    Waves, Cookie, Pencil, BedDouble, Bath, ShowerHead, Backpack,
    CookingPot, Briefcase,
} from 'lucide-react';
import { RoutineFlowState, RoutineStep } from '@/lib/routineDataSchema';

const ICONS: Record<string, LucideIcon> = {
    wash: Droplet,
    meal: UtensilsCrossed,
    clothes: Shirt,
    teeth: Sparkles,
    toilet: Bath,
    free: Star,
    handwash: Waves,
    snack: Cookie,
    homework: Pencil,
    // 「明日の準備」(要件: 宿題の次に追加)。
    tomorrow_prep: Backpack,
    // 「お風呂」(要件: 寝る準備を晩ごはん/お風呂/着替え/歯磨きに分割)。
    // トイレはlucide-reactに専用アイコンが無いため既にBathを転用しており、
    // 実際の入浴と見分けが付くようShowerHeadを充てる。
    bath: ShowerHead,
    sleep: BedDouble,
    // 大人用フロー(routine_data.py DAD/MOM_ROUTINE_FLOWS)のタスクステップ。
    // 'meal'(UtensilsCrossed)は「食べる」側で使っているため、ママの「夕食を作る」は
    // 調理器具のCookingPotで「作る」側と見分けが付くようにする。
    kitchen: CookingPot,
    work: Briefcase,
};

const THEME = {
    am: {
        text: 'text-yellow-400',
        chip: 'bg-yellow-900/40 text-yellow-300 border-yellow-500',
        node: 'bg-gradient-to-br from-yellow-300 to-yellow-500 text-yellow-950 border-yellow-300',
        nodeDone: 'bg-yellow-900/40 border-yellow-500 text-yellow-300',
        seg: 'bg-yellow-500',
        spotlight: 'bg-gradient-to-br from-yellow-900/50 to-yellow-900/10 border-yellow-500/60',
        btn: 'bg-gradient-to-r from-yellow-300 to-yellow-500 text-yellow-950 shadow-yellow-500/40',
        banner: 'bg-yellow-900/30 border-yellow-500/50',
    },
    pm: {
        text: 'text-purple-400',
        chip: 'bg-purple-900/40 text-purple-300 border-purple-500',
        node: 'bg-gradient-to-br from-purple-300 to-purple-500 text-purple-950 border-purple-300',
        nodeDone: 'bg-purple-900/40 border-purple-500 text-purple-300',
        seg: 'bg-purple-500',
        spotlight: 'bg-gradient-to-br from-purple-900/50 to-purple-900/10 border-purple-500/60',
        btn: 'bg-gradient-to-r from-purple-300 to-purple-500 text-white shadow-purple-500/40',
        banner: 'bg-purple-900/30 border-purple-500/50',
    },
} as const;

interface RoutineFlowProps {
    flowKey: 'am' | 'pm';
    flow: RoutineFlowState;
    onCompleteStep: (stepKey: string) => void;
    isCompleting?: boolean;
    compact?: boolean;
}

// 自由時間中は別画面(既存のクエスト選択UI)に任せ、ここでは現在地バナーだけを表示する。
export const RoutineFreeTimeBanner: React.FC<{ flowKey: 'am' | 'pm'; flow: RoutineFlowState }> = ({ flowKey, flow }) => {
    if (!flow.started || !flow.in_free_time) return null;
    const theme = THEME[flowKey];
    return (
        <div className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-xs ${theme.banner}`}>
            <Star size={16} className={theme.text} />
            <div className="flex-1 min-w-0">
                <div className={`font-bold ${theme.text}`}>自由時間中</div>
                {flow.checkpoint_time && (
                    <div className="text-gray-300 truncate">{flow.checkpoint_time} になったら次へ進むよ</div>
                )}
            </div>
        </div>
    );
};

const RoutineFlow: React.FC<RoutineFlowProps> = ({ flowKey, flow, onCompleteStep, isCompleting, compact }) => {
    if (!flow.started) return null;
    const theme = THEME[flowKey];

    if (flow.is_complete) {
        return (
            <div className={`rounded-2xl border p-5 text-center ${theme.spotlight}`}>
                <div className="text-3xl mb-1">🎉</div>
                <div className={`font-bold ${theme.text}`}>{flow.title}・完了！</div>
            </div>
        );
    }

    if (flow.in_free_time) {
        return <RoutineFreeTimeBanner flowKey={flowKey} flow={flow} />;
    }

    // チェックリスト項目(順不同でチェックできる、例: 朝の準備5項目・寝る準備4項目)は
    // フロー内で連続する一塊(routine_data.py get_checklist_range参照)。すごろく/
    // 路線図の一本道には乗せず、専用のチェックボックスUIとしてひとまとめに表示するが、
    // ブロックの位置自体はamなら先頭、pmならチェックポイント通過後と実際のflow.steps
    // 上の並び順が異なるため、単純に「チェックリスト→残りの一本道」の順で並べると
    // pmでは表示順が生活動線と逆転してしまう。そのためflow.steps全体を1回だけ走査し、
    // チェックリストに差し掛かった位置でブロックを1つだけ差し込む形で組み立てる。
    const checklistSteps = flow.steps.filter((step) => step.is_checklist);
    const pathSteps = flow.steps.filter((step) => !step.is_checklist);
    const lastPathKey = pathSteps.length > 0 ? pathSteps[pathSteps.length - 1].key : null;
    // 出発ボーナス(preview_bonus_gold)はチェックポイントより前のステップの達成率で
    // 決まる(routine_service._eligible_done_ratio)。チェックリストがチェックポイントの
    // 「後」にある場合(例: pmの寝る準備4項目)は、その時点でボーナスは既に確定済みで
    // チェックしても金額は変わらないため、ボーナス表示自体を出さない(要件確認済みの
    // 範囲外だが、出しっぱなしだと「チェックすると増える」ように誤解させてしまうため)。
    const checkpointIndex = flow.steps.findIndex((step) => step.is_checkpoint);
    const checklistStartIndex = flow.steps.findIndex((step) => step.is_checklist);
    const checklistAffectsBonus = checkpointIndex === -1 || checklistStartIndex < checkpointIndex;

    let checklistRendered = false;
    const rows = flow.steps.reduce<React.ReactNode[]>((acc, step) => {
        if (step.is_checklist) {
            if (!checklistRendered) {
                checklistRendered = true;
                acc.push(
                    <RoutineChecklistBlock
                        key="checklist-block"
                        flowKey={flowKey}
                        steps={checklistSteps}
                        previewBonusGold={checklistAffectsBonus ? flow.preview_bonus_gold : null}
                        fullBonusGold={flow.bonus_full_gold}
                        onToggleStep={onCompleteStep}
                        isCompleting={!!isCompleting}
                        compact={!!compact}
                    />
                );
            }
            return acc;
        }
        acc.push(
            <RoutineStepRow
                key={step.key}
                flowKey={flowKey}
                step={step}
                checkpointTime={flow.checkpoint_time}
                isLast={step.key === lastPathKey}
                onComplete={() => onCompleteStep(step.key)}
                isCompleting={!!isCompleting}
                compact={!!compact}
            />
        );
        return acc;
    }, []);

    return <div className="flex flex-col">{rows}</div>;
};

// 朝の準備等、順不同でチェックできる項目をまとめて表示するブロック。チェックポイント
// より前のチェックリスト(例: 朝の準備)ではチェックのたびに出発ボーナスの見込み額
// (preview_bonus_gold)が増えていく様子も併せて見せる(要件: 1つチェックすると出発
// ゴールドが増え、それが画面で分かるようにしたい)。previewBonusGoldがnullの場合
// (チェックポイント後のチェックリスト、例: pmの寝る準備4項目)はボーナス表示自体を省く
// (ボーナスは既に確定済みでチェックしても金額が変わらないため、誤解を避ける)。
const RoutineChecklistBlock: React.FC<{
    flowKey: 'am' | 'pm';
    steps: RoutineStep[];
    previewBonusGold: number | null;
    fullBonusGold: number;
    onToggleStep: (stepKey: string) => void;
    isCompleting: boolean;
    compact: boolean;
}> = ({ flowKey, steps, previewBonusGold, fullBonusGold, onToggleStep, isCompleting, compact }) => {
    const theme = THEME[flowKey];
    return (
        <div className={`rounded-2xl border p-4 mb-4 ${theme.spotlight}`}>
            <div className="flex items-center justify-between gap-2 mb-3">
                <span className={`text-xs font-bold ${theme.text}`}>できたらチェック！(じゅんばんは自由だよ)</span>
                {previewBonusGold !== null && (
                    <span className={`flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-bold whitespace-nowrap ${theme.chip}`}>
                        <Coins size={12} />出発ボーナス {previewBonusGold} / {fullBonusGold}
                    </span>
                )}
            </div>
            <div className="flex flex-col gap-2">
                {steps.map((step) => (
                    <RoutineChecklistItem
                        key={step.key}
                        flowKey={flowKey}
                        step={step}
                        onToggle={() => onToggleStep(step.key)}
                        isCompleting={isCompleting}
                        compact={compact}
                    />
                ))}
            </div>
        </div>
    );
};

const RoutineChecklistItem: React.FC<{
    flowKey: 'am' | 'pm';
    step: RoutineStep;
    onToggle: () => void;
    isCompleting: boolean;
    compact: boolean;
}> = ({ flowKey, step, onToggle, isCompleting, compact }) => {
    const theme = THEME[flowKey];
    const Icon = ICONS[step.icon_key] || Star;
    const checked = step.status === 'done';
    const remind = step.status === 'remind';
    const iconSize = compact ? 14 : 18;

    let boxClass = 'border-gray-500 text-gray-400';
    if (checked) boxClass = theme.node;
    if (remind) boxClass = 'border-orange-500 text-orange-300';

    let rowClass = 'border-gray-600 bg-gray-800/60';
    if (checked) rowClass = theme.nodeDone;
    if (remind) rowClass = 'bg-orange-900/40 border-orange-500';

    return (
        <button
            type="button"
            onClick={onToggle}
            disabled={isCompleting}
            className={`flex items-center gap-3 w-full rounded-xl border px-3 py-2.5 text-left transition-colors disabled:opacity-50 ${rowClass}`}
        >
            <span className={`flex items-center justify-center rounded-full border-2 flex-none ${compact ? 'w-7 h-7' : 'w-9 h-9'} ${boxClass}`}>
                {checked ? <Check size={iconSize} /> : remind ? <Bell size={iconSize} /> : <Icon size={iconSize} />}
            </span>
            <span className={`flex-1 font-bold ${compact ? 'text-sm' : 'text-base'} ${checked ? theme.text : remind ? 'text-orange-300' : 'text-gray-200'}`}>
                {step.label}
            </span>
            {remind && (
                <span className="rounded-full bg-orange-900/40 text-orange-300 text-[10px] font-bold px-2 py-0.5 flex-none">まだだよ</span>
            )}
        </button>
    );
};

// ステップ個別の即時報酬(大人用フローに寄せたデイリークエスト相当)の表示。
// 子ども用フローのステップは gold/exp とも0のため、何も描画しない。
const RoutineStepRewardChip: React.FC<{ flowKey: 'am' | 'pm'; step: RoutineStep }> = ({ flowKey, step }) => {
    if (!step.gold && !step.exp) return null;
    return (
        <span className={`flex items-center gap-0.5 rounded-full border px-2 py-0.5 text-[10px] font-bold whitespace-nowrap flex-none ${THEME[flowKey].chip}`}>
            <Coins size={10} />{step.gold}
        </span>
    );
};

const RoutineStepRow: React.FC<{
    flowKey: 'am' | 'pm';
    step: RoutineStep;
    checkpointTime: string | null;
    isLast: boolean;
    onComplete: () => void;
    isCompleting: boolean;
    compact: boolean;
}> = ({ flowKey, step, checkpointTime, isLast, onComplete, isCompleting, compact }) => {
    const theme = THEME[flowKey];
    const Icon = ICONS[step.icon_key] || Star;
    const nodeSize = compact ? 'w-8 h-8' : (step.status === 'current' ? 'w-14 h-14' : 'w-11 h-11');
    const iconSize = compact ? 14 : (step.status === 'current' ? 24 : 18);

    let nodeClass = 'border-gray-600 bg-gray-800 text-gray-500';
    if (step.status === 'done') nodeClass = theme.nodeDone;
    if (step.status === 'current') nodeClass = `${theme.node} ${!compact ? 'animate-pulse' : ''}`;
    if (step.status === 'remind') nodeClass = 'bg-orange-900/40 border-orange-500 text-orange-300';

    return (
        <div className="flex gap-3">
            <div className="flex flex-col items-center flex-none" style={{ width: compact ? 32 : 44 }}>
                <div className={`rounded-full border-2 flex items-center justify-center flex-none transition-all ${nodeClass} ${nodeSize}`}>
                    {step.status === 'done' ? <Check size={iconSize} /> : step.status === 'remind' ? <Bell size={iconSize} /> : <Icon size={iconSize} />}
                </div>
                {step.is_checkpoint ? (
                    // チェックポイントの時刻表示は「次のステップへの接続線」ではなく
                    // 「この時刻に自動で進む/完了する」という告知のため、そのステップが
                    // フロー最後(isLast)でも出し続ける(例: amは'free'がチェックポイント
                    // 兼フロー最後のステップ、画面非表示化で'leave'廃止のため)。
                    <span className={`my-1 flex items-center gap-1 rounded-full border border-dashed px-2 py-0.5 text-[10px] font-bold whitespace-nowrap ${theme.chip}`}>
                        <Clock size={10} />{checkpointTime}
                    </span>
                ) : (
                    !isLast && (
                        <div className={`w-[3px] flex-1 min-h-[16px] rounded-full ${step.status === 'done' ? theme.seg : 'bg-gray-700'}`} />
                    )
                )}
            </div>

            <div className={`flex-1 min-w-0 ${compact ? 'pb-2' : 'pb-5'}`}>
                {step.status === 'current' ? (
                    <div className={`rounded-2xl border p-4 ${theme.spotlight}`}>
                        <div className={`flex items-center gap-1 text-xs font-bold mb-1 ${theme.text}`}>🌟 今ここ</div>
                        <div className="flex items-center gap-2 mb-3">
                            <div className="text-xl font-black text-white min-w-0">{step.label}</div>
                            <RoutineStepRewardChip flowKey={flowKey} step={step} />
                        </div>
                        <button
                            type="button"
                            onClick={onComplete}
                            disabled={isCompleting}
                            className={`w-full rounded-full py-3 font-black shadow-lg disabled:opacity-50 ${theme.btn}`}
                        >
                            完了！
                        </button>
                    </div>
                ) : (
                    <div className={`flex items-center gap-1.5 h-full ${compact ? 'text-xs' : 'text-sm'} ${step.status === 'locked' ? 'text-gray-500' : 'text-gray-300'}`}>
                        {step.status === 'remind' && <span className="rounded-full bg-orange-900/40 text-orange-300 text-[10px] font-bold px-2 py-0.5 flex-none">まだだよ</span>}
                        <span className="min-w-0">{step.label}</span>
                        <RoutineStepRewardChip flowKey={flowKey} step={step} />
                    </div>
                )}
            </div>
        </div>
    );
};

export default RoutineFlow;
