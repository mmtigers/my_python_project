import React, { useState } from 'react';
import { Modal } from '@/components/ui/Modal';
import { apiClient } from '@/lib/apiClient';
import { CameraConfig } from '../types';

interface Props {
    isOpen: boolean;
    onClose: () => void;
    cameras: CameraConfig[];
    onToggled: () => Promise<void> | void;
}

// カメラごとの表示/非表示を切り替える設定パネル。
// 無効化したカメラは devices.json に永続化され、ライブ/録画タブの一覧から除外される。
const CameraSettingsModal: React.FC<Props> = ({ isOpen, onClose, cameras, onToggled }) => {
    const [pendingId, setPendingId] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const handleToggle = async (camera: CameraConfig) => {
        setPendingId(camera.id);
        setError(null);
        try {
            await apiClient.put(`/api/cameras/settings/${camera.id}`, { enabled: !camera.enabled });
            await onToggled();
        } catch (err) {
            console.error('Failed to update camera settings:', err);
            setError('設定の更新に失敗しました。');
        } finally {
            setPendingId(null);
        }
    };

    return (
        <Modal isOpen={isOpen} onClose={onClose} title="カメラ設定" maxWidth="md">
            <div className="space-y-3">
                <p className="text-xs text-slate-400">
                    表示したいカメラだけをオンにしてください。オフにしたカメラはライブ/録画の一覧に表示されなくなります。
                </p>
                {error && <p className="text-xs text-red-400">{error}</p>}
                <div className="space-y-2">
                    {cameras.map(camera => (
                        <label
                            key={camera.id}
                            className="flex items-center gap-3 bg-slate-900/60 border border-slate-700 rounded-lg px-3 py-2 cursor-pointer"
                        >
                            <input
                                type="checkbox"
                                checked={camera.enabled}
                                disabled={pendingId === camera.id}
                                onChange={() => handleToggle(camera)}
                                className="w-5 h-5 accent-blue-500"
                            />
                            <span className="text-white font-bold text-sm flex-1">{camera.name}</span>
                        </label>
                    ))}
                    {cameras.length === 0 && (
                        <p className="text-sm text-slate-400">登録されているカメラがありません。</p>
                    )}
                </div>
            </div>
        </Modal>
    );
};

export default CameraSettingsModal;
