import React, { useState, useEffect } from 'react';
import LiveView from './LiveView';
import RecordView from './RecordView';
import { CameraConfig } from '../types';
import { Camera } from 'lucide-react';

const CameraDashboard: React.FC = () => {
    const [cameras, setCameras] = useState<CameraConfig[]>([]);
    const [activeTab, setActiveTab] = useState<'live' | 'record'>('live');
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        document.title = "ホーム監視カメラ";
        fetch('/api/cameras/settings')
            .then(res => res.json())
            .then(data => {
                const activeCameras = data.filter((c: CameraConfig) => c.enabled);
                activeCameras.sort((a: CameraConfig, b: CameraConfig) => a.order - b.order);
                setCameras(activeCameras);
                setLoading(false);
            })
            .catch(err => {
                console.error("Failed to fetch camera settings:", err);
                setLoading(false);
            });
        return () => { document.title = "Family Quest"; };
    }, []);

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
                </header>

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
        </div>
    );
};

export default CameraDashboard;