import { Modal } from './Modal';
import { Button } from './Button';
import { Quest, QuestHistory, Reward } from '@/types';

// ConfirmModal の target に渡りうる型。モードごとに実際に持っているプロパティが異なるため、
// メッセージ生成はモードごとに個別にキャストして組み立てる（getMessage 内）。
// ★実機検証で子どもの誤操作が多かったため、クエスト完了(クリア)には確認ダイアログを復活させた。
// 取り消しは長押しでのみ発火する(QuestList側のuseLongPress)ため、引き続き確認なしのワンタップとする。
export type ConfirmTarget = Quest | QuestHistory | Reward;

// 却下理由のプリセット。自由入力の手間を省き、あとで見返した時にも理由がわかるようにする。
const REJECT_REASONS = ['写真が不明瞭', 'まだ終わっていない', '重複している', 'その他'];

interface ConfirmModalProps {
    mode: 'complete' | 'purchase' | 'reject' | null;
    target: ConfirmTarget | null;
    rejectReason: string | null;
    onSelectRejectReason: (reason: string) => void;
    onConfirm: () => void;
    onCancel: () => void;
    isConfirming: boolean;
}

export const ConfirmModal = ({
    mode, target, rejectReason, onSelectRejectReason, onConfirm, onCancel, isConfirming
}: ConfirmModalProps) => {
    if (!mode || !target) return null;

    const getMessage = (): { title: string; text: string } => {
        switch (mode) {
            case 'complete': {
                const t = target as Quest;
                return { title: 'クエスト完了', text: `「${t.title}」を完了にしますか？` };
            }
            case 'purchase': {
                const t = target as Reward;
                // #291: masterData.js のフォールバック報酬も含め cost_gold に一本化したため、
                // cost へのフォールバックは不要になった。
                return { title: 'アイテム購入', text: `「${t.title}」を ${t.cost_gold}G で買いますか？` };
            }
            case 'reject':
                return { title: '却下確認', text: '本当に却下しますか？' };
        }
    };
    const msg = getMessage();

    return (
        // #394: 応答待ち中(isConfirming)は背景タップ/ESC/×ボタンのいずれでも閉じられない
        // ようにする(閉じてもリクエストは継続するため、「モーダルを残して再試行できる
        // ようにする」という設計意図が崩れてしまう)。
        <Modal isOpen={true} onClose={onCancel} title={msg.title} preventClose={isConfirming}>
            <div className="p-4">
                <p className="whitespace-pre-wrap text-center mb-4">{msg.text}</p>

                {/* 角度⑫: 却下理由をプリセットからワンタップで選べるようにし、自由入力の手間を省く */}
                {mode === 'reject' && (
                    <div className="flex flex-wrap gap-2 justify-center mb-6">
                        {REJECT_REASONS.map(r => (
                            <button
                                key={r}
                                onClick={() => onSelectRejectReason(r)}
                                className={`min-h-[36px] px-3 py-1.5 rounded-full text-xs font-bold border-2 transition-colors ${rejectReason === r
                                    ? 'bg-red-600 border-red-400 text-white'
                                    : 'bg-slate-800 border-slate-600 text-slate-300 hover:border-slate-400'
                                    }`}
                            >
                                {r}
                            </button>
                        ))}
                    </div>
                )}

                <div className="flex gap-4 justify-center">
                    <Button variant="secondary" onClick={onCancel} disabled={isConfirming}>キャンセル</Button>
                    <Button variant="primary" onClick={onConfirm} isLoading={isConfirming}>はい</Button>
                </div>
            </div>
        </Modal>
    );
};
