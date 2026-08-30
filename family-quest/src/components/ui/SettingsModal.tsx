import React from 'react';
import { Modal } from './Modal';
import { Button } from './Button';
import { useSettings } from '@/context/useSettings';
import { THEME_COLORS } from '@/context/settingsShared';
import { User } from '@/types';
import { LayoutGrid, Rows3 } from 'lucide-react';
import { isSameOriginAvatarPath } from '@/lib/utils';

interface Props {
    isOpen: boolean;
    onClose: () => void;
    users: User[];
}

// 表示密度・非識字モード対象ユーザー・ユーザーごとのテーマカラーをまとめて設定する画面。
// 以前は「アイコン主体表示」がコード上に固定されているだけで、誰も画面から変更できなかった。
const SettingsModal: React.FC<Props> = ({ isOpen, onClose, users }) => {
    const { density, setDensity, iconFirstUserIds, toggleIconFirstUser, userThemeColors, setUserThemeColor } = useSettings();

    return (
        <Modal isOpen={isOpen} onClose={onClose} title="表示せってい" maxWidth="md">
            <div className="space-y-6">
                {/* 表示密度 */}
                <section>
                    <h4 className="text-sm font-bold text-slate-300 mb-2">表示密度</h4>
                    <div className="flex gap-2">
                        <Button
                            variant={density === 'comfortable' ? 'primary' : 'outline'}
                            size="sm"
                            className="flex-1"
                            onClick={() => setDensity('comfortable')}
                        >
                            <Rows3 size={16} className="mr-1" /> ゆったり
                        </Button>
                        <Button
                            variant={density === 'compact' ? 'primary' : 'outline'}
                            size="sm"
                            className="flex-1"
                            onClick={() => setDensity('compact')}
                        >
                            <LayoutGrid size={16} className="mr-1" /> コンパクト
                        </Button>
                    </div>
                </section>

                {/* 非識字モード対象ユーザー */}
                <section>
                    <h4 className="text-sm font-bold text-slate-300 mb-1">アイコン主体表示（文字が読めない子ども向け）</h4>
                    <p className="text-xs text-slate-500 mb-2">選んだ人のクエスト一覧は、説明文を隠してアイコンを大きく表示します。</p>
                    <div className="space-y-2">
                        {users.map(user => {
                            const checked = iconFirstUserIds.includes(user.user_id);
                            return (
                                <label
                                    key={user.user_id}
                                    className="flex items-center gap-3 bg-slate-900/60 border border-slate-700 rounded-lg px-3 py-2 cursor-pointer"
                                >
                                    <input
                                        type="checkbox"
                                        checked={checked}
                                        onChange={() => toggleIconFirstUser(user.user_id)}
                                        className="w-5 h-5 accent-yellow-500"
                                    />
                                    {/* ★バグ修正: バックエンド(quest_users)にicon列は存在せずavatar列のみのため、
                                        user.iconは常にundefinedで全ユーザーが'🙂'固定表示になっていた。
                                        user.avatarを参照するよう修正(絵文字デフォルト値または、写真アップロード済み
                                        なら/uploads/...のパスなので、AvatarUploader.tsx等と同じisSameOriginAvatarPath
                                        判定でパス形式なら<img>、それ以外は絵文字テキストとして表示する) */}
                                    {isSameOriginAvatarPath(user.avatar) ? (
                                        <img src={user.avatar} alt={user.name} className="w-6 h-6 rounded-full object-cover flex-shrink-0" />
                                    ) : (
                                        <span className="text-xl">{user.avatar || '🙂'}</span>
                                    )}
                                    <span className="text-white font-bold text-sm">{user.name}</span>
                                </label>
                            );
                        })}
                    </div>
                </section>

                {/* ユーザーごとのテーマカラー */}
                <section>
                    <h4 className="text-sm font-bold text-slate-300 mb-2">パネルのアクセントカラー（横画面表示）</h4>
                    <div className="space-y-2">
                        {users.map(user => (
                            <div key={user.user_id} className="flex items-center gap-3">
                                <span className="text-sm text-white w-16 truncate">{user.name}</span>
                                <div className="flex gap-1.5 flex-wrap">
                                    {THEME_COLORS.map(color => (
                                        <button
                                            key={color.key}
                                            aria-label={color.label}
                                            onClick={() => setUserThemeColor(user.user_id, color.key)}
                                            className={`w-6 h-6 rounded-full ${color.className} border-2 transition-all ${userThemeColors[user.user_id] === color.key
                                                ? 'border-white scale-110'
                                                : 'border-transparent opacity-70 hover:opacity-100'
                                                }`}
                                        />
                                    ))}
                                </div>
                            </div>
                        ))}
                    </div>
                </section>
            </div>
        </Modal>
    );
};

export default SettingsModal;
