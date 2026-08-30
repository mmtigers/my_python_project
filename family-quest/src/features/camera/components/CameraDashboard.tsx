import React, { useState, useEffect, useCallback, useMemo } from 'react';
import LiveView from './LiveView';
import RecordView from './RecordView';
import CameraSettingsModal from './CameraSettingsModal';
import { CameraConfig } from '../types';
import { Camera, Settings } from 'lucide-react';
import { apiClient } from '@/lib/apiClient';

// ★バグ修正(Issue #121): apiClient側でスローされるErrorのmessageには、バックエンドが
// 返す{"detail": "..."}の内容が入っている(apiClient.ts参照)。CameraDashboardは
// main.tsxでToastProvider配下ではなく独立してマウントされるため(/cameraは
// Family Quest本体と同時に使われない専用ビューア)、他画面のようにuseToast()の
// showToastは使えない。代わりにローカルなfetchErrorステートで画面内にエラー表示する。
const extractErrorDetail = (error: unknown): string => {
    return error instanceof Error && error.message ? error.message : 'カメラ設定の取得に失敗しました';
};

const CameraDashboard: React.FC = () => {
    const [allCameras, setAllCameras] = useState<CameraConfig[]>([]);
    const [activeTab, setActiveTab] = useState<'live' | 'record'>('live');
    const [loading, setLoading] = useState(true);
    const [settingsOpen, setSettingsOpen] = useState(false);
    // ★バグ修正(Issue #121): 以前はcatchがconsole.errorのみで、バックエンド停止・
    // ネットワーク断時にライブタブが「無言で」空のグリッドを表示していた
    // (障害の原因がユーザーに一切伝わらない)。取得失敗を画面上のバナーで通知する。
    const [fetchError, setFetchError] = useState<string | null>(null);

    const fetchSettings = useCallback(() => {
        return apiClient.get<CameraConfig[]>('/api/cameras/settings')
            .then(data => {
                setAllCameras([...data].sort((a, b) => a.order - b.order));
                setFetchError(null);
            })
            .catch(err => {
                console.error("Failed to fetch camera settings:", err);
                setFetchError(extractErrorDetail(err));
            });
    }, []);

    useEffect(() => {
        document.title = "ホーム監視カメラ";
        fetchSettings().finally(() => setLoading(false));
        return () => { document.title = "Family Quest"; };
    }, [fetchSettings]);

    const cameras = useMemo(() => allCameras.filter(c => c.enabled), [allCameras]);

    if (loading) return <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center p-8">読み込み中...</div>;

    return (
        // 独立した全画面レイアウト
        <div className="min-h-screen bg-gray-900 text-gray-100 p-4 md:p-8 font-sans">
            <div className="max-w-6xl mx-auto">
                {/* 独立したヘッダー */}
                <header className="mb-6 flex items-center justify-between border-b border-gray-700 pb-4">
                    <h1 className="text-xl md:text-2xl font-bold flex items-center gap-2">
                        <Camera size={28} className="text-blue-500" />
                        ホーム監視カメラ
                    </h1>
                    <button
                        aria-label="カメラ設定"
                        className="p-2 rounded-full text-gray-300 hover:bg-gray-800 hover:text-white transition-colors"
                        onClick={() => setSettingsOpen(true)}
                    >
                        <Settings size={22} />
                    </button>
                </header>

                {fetchError && (
                    <div className="mb-6 flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-lg border border-red-700 bg-red-950/40 px-4 py-3 text-sm text-red-300">
                        <span>⚠️ カメラ設定の取得に失敗しました: {fetchError}</span>
                        <button
                            onClick={() => { setLoading(true); fetchSettings().finally(() => setLoading(false)); }}
                            className="shrink-0 rounded bg-red-800 px-3 py-1 font-bold text-white hover:bg-red-700 transition-colors"
                        >
                            再試行
                        </button>
                    </div>
                )}

                <div className="flex gap-2 mb-6 pb-2">
                    <button
                        className={`px-6 py-2 font-bold rounded transition-colors ${activeTab === 'live' ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-300 hover:bg-gray-700'}`}
                        onClick={() => setActiveTab('live')}
                    >
                        🟢 ライブ映像
                    </button>
                    <button
                        className={`px-6 py-2 font-bold rounded transition-colors ${activeTab === 'record' ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-300 hover:bg-gray-700'}`}
                        onClick={() => setActiveTab('record')}
                    >
                        📼 録画再生
                    </button>
                </div>

                {activeTab === 'live' ? (
                    <LiveView cameras={cameras} />
                ) : (
                    <RecordView cameras={cameras} />
                )}
            </div>

            <CameraSettingsModal
                isOpen={settingsOpen}
                onClose={() => setSettingsOpen(false)}
                cameras={allCameras}
                onToggled={fetchSettings}
            />
        </div>
    );
};

export default CameraDashboard;