import React from 'react';
import { Modal } from './Modal';
import { useToast } from '@/context/useToast';

interface Props {
    isOpen: boolean;
    onClose: () => void;
}

const formatTime = (ts: number) =>
    new Date(ts).toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' });

// トーストとして流れて消えた通知(レベルアップ・メダル獲得・購入完了など)を、
// あとからまとめて見返せるようにする一覧パネル。
const NotificationHistoryPanel: React.FC<Props> = ({ isOpen, onClose }) => {
    const { history, clearHistory } = useToast();

    return (
        <Modal isOpen={isOpen} onClose={onClose} title="おしらせ履歴" maxWidth="md">
            {history.length === 0 ? (
                <p className="text-center text-slate-400 py-6">まだおしらせはありません</p>
            ) : (
                <div className="space-y-2 max-h-[60vh] overflow-y-auto">
                    {history.map(item => (
                        <div key={item.id} className="flex items-start gap-3 bg-slate-900/60 border border-slate-700 rounded-lg p-3">
                            {item.icon && <span className="text-2xl leading-none">{item.icon}</span>}
                            <div className="flex-1 min-w-0">
                                <div className="flex items-baseline justify-between gap-2">
                                    <span className="font-bold text-yellow-400 text-sm">{item.title}</span>
                                    <span className="text-[10px] text-slate-500 whitespace-nowrap">{formatTime(item.createdAt)}</span>
                                </div>
                                {item.text && <div className="text-slate-300 text-xs mt-0.5">{item.text}</div>}
                            </div>
                        </div>
                    ))}
                </div>
            )}

            {history.length > 0 && (
                <button
                    onClick={clearHistory}
                    className="mt-4 w-full text-center text-xs text-slate-400 underline hover:text-slate-200"
                >
                    履歴を消す
                </button>
            )}
        </Modal>
    );
};

export default NotificationHistoryPanel;
