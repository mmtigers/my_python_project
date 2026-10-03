import React from 'react';
import { Modal } from './Modal';
import { CHANGELOG } from '@/lib/changelog';

interface Props {
    isOpen: boolean;
    onClose: () => void;
}

// アプリの更新履歴(バージョン・日付・変更点)を新しい順に表示するモーダル。
// データの正は src/lib/changelog.ts。
const ChangelogModal: React.FC<Props> = ({ isOpen, onClose }) => {
    return (
        <Modal isOpen={isOpen} onClose={onClose} title="アップデートのれきし" maxWidth="md">
            <div className="space-y-5 max-h-[60vh] overflow-y-auto">
                {CHANGELOG.map(entry => (
                    <section key={entry.version}>
                        <div className="flex items-baseline gap-2 mb-1">
                            <h4 className="text-sm font-bold text-yellow-400">v{entry.version}</h4>
                            <span className="text-xs text-slate-500">{entry.date}</span>
                        </div>
                        <ul className="list-disc pl-5 space-y-1 text-sm text-slate-200">
                            {entry.changes.map(change => (
                                <li key={change}>{change}</li>
                            ))}
                        </ul>
                    </section>
                ))}
            </div>
        </Modal>
    );
};

export default ChangelogModal;
