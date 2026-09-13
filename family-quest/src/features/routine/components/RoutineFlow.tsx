// family-quest/src/features/routine/components/RoutineFlow.tsx
//
// 「きょうのすごろく」— 平日の朝/夕方の生活動線を、縦一列のすごろく/路線図として
// 常時全ステップ表示するUI(療育目的: 現在地と見通しを常に見せる)。
// 自由時間の終了(チェックポイント)だけはサーバー側で時刻ベースに強制通過させる
// (services/routine_service.py)ため、このコンポーネントには手動で進める手段を
// 持たせず、現在のステップの完了報告のみを行う。
import React from 'react';
import { Bell, Check, Clock, LucideIcon } from 'lucide-react';
import {
    Droplet, UtensilsCrossed, Shirt, Sparkles, Star, DoorOpen,
    Waves, Cookie, Pencil, Moon, BedDouble,
} from 'lucide-react';
import { RoutineFlowState, RoutineStep } from '@/lib/routineDataSchema';

const ICONS: Record<string, LucideIcon> = {
    wash: Droplet,
    meal: UtensilsCrossed,
    clothes: Shirt,
    teeth: Sparkles,
    free: Star,
    leave: DoorOpen,
    handwash: Waves,
    snack: Cookie,
    homework: Pencil,
    nightprep: Moon,
    sleep: BedDouble,
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

    return (
        <div className="flex flex-col">
            {flow.steps.map((step, idx) => (
                <RoutineStepRow
                    key={step.key}
                    flowKey={flowKey}
                    step={step}
                    checkpointTime={flow.checkpoint_time}
                    isLast={idx === flow.steps.length - 1}
                    onComplete={() => onCompleteStep(step.key)}
                    isCompleting={!!isCompleting}
                    compact={!!compact}
                />
            ))}
        </div>
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
                {!isLast && (
                    step.is_checkpoint ? (
                        <span className={`my-1 flex items-center gap-1 rounded-full border border-dashed px-2 py-0.5 text-[10px] font-bold whitespace-nowrap ${theme.chip}`}>
                            <Clock size={10} />{checkpointTime}
                        </span>
                    ) : (
                        <div className={`w-[3px] flex-1 min-h-[16px] rounded-full ${step.status === 'done' ? theme.seg : 'bg-gray-700'}`} />
                    )
                )}
            </div>

            <div className={`flex-1 min-w-0 ${compact ? 'pb-2' : 'pb-5'}`}>
                {step.status === 'current' ? (
                    <div className={`rounded-2xl border p-4 ${theme.spotlight}`}>
                        <div className={`flex items-center gap-1 text-xs font-bold mb-1 ${theme.text}`}>🌟 今ここ</div>
                        <div className="text-xl font-black text-white mb-3">{step.label}</div>
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
                    <div className={`flex items-center h-full ${compact ? 'text-xs' : 'text-sm'} ${step.status === 'locked' ? 'text-gray-500' : 'text-gray-300'}`}>
                        {step.status === 'remind' && <span className="mr-1.5 rounded-full bg-orange-900/40 text-orange-300 text-[10px] font-bold px-2 py-0.5">まだだよ</span>}
                        {step.label}
                    </div>
                )}
            </div>
        </div>
    );
};

export default RoutineFlow;
