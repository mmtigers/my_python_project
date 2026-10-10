import React from 'react';
import { X } from 'lucide-react';
import { ChangelogEntry } from '../../lib/changelog';

interface Props {
    entry: ChangelogEntry;
    // 「見る」をタップ: アップデートのれきしを開く(既読化は呼び出し側)。
    onOpen: () => void;
    // ×をタップ: 見ずに閉じる(既読化は呼び出し側)。
    onDismiss: () => void;
}

// 新しいアップデートがあるときだけ、画面上部に出す案内の帯。
// 履歴ボタン(右上の小さなアイコン)だけでは家族が気づけないための目立つ導線。
const UpdateNoticeBanner: React.FC<Props> = ({ entry, onOpen, onDismiss }) => (
    <div className="px-3 pt-3 max-w-md md:max-w-5xl mx-auto w-full">
        <div
            role="status"
            className="flex items-center gap-2 p-3 rounded-xl border text-sm bg-emerald-50 border-emerald-400 text-emerald-900"
        >
            <span className="text-xl leading-none flex-shrink-0">🆕</span>
            <p className="flex-1 min-w-0">
                <span className="font-bold">アップデートがありました</span>
                <span className="opacity-70">(v{entry.version})</span>
            </p>
            <button
                type="button"
                onClick={onOpen}
                className="min-h-[44px] px-3 rounded-lg bg-emerald-600 text-white font-bold text-xs flex-shrink-0"
            >
                見る
            </button>
            <button
                type="button"
                onClick={onDismiss}
                aria-label="アップデートのお知らせを閉じる"
                className="w-11 h-11 flex items-center justify-center rounded-full text-emerald-700 flex-shrink-0"
            >
                <X size={18} />
            </button>
        </div>
    </div>
);

export default UpdateNoticeBanner;
