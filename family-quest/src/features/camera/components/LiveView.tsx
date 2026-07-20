import React, { useState } from 'react';
import HlsPlayer from '../../../components/ui/HlsPlayer';
import { CameraConfig } from '../types';

interface LiveViewProps {
    cameras: CameraConfig[];
}

const LiveView: React.FC<LiveViewProps> = ({ cameras }) => {
    const [selectedCamera, setSelectedCamera] = useState<string | null>(null);
    const isSingleView = selectedCamera !== null;

    return (
        <div className="w-full">
            {isSingleView && (
                <div className="flex flex-col items-center">
                    <button
                        className="mb-4 px-6 py-2 bg-gray-600 text-white rounded font-bold"
                        onClick={() => setSelectedCamera(null)}
                    >
                        ◀ 4分割に戻る
                    </button>
                    <div className="w-full max-w-4xl aspect-video bg-black rounded shadow-lg overflow-hidden">
                        <HlsPlayer streamUrl={`/api/cameras/live/${selectedCamera}/stream.m3u8`} controls />
                    </div>
                </div>
            )}

            {!isSingleView && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {cameras.map(camera => (
                        <div
                            key={camera.id}
                            className="cursor-pointer border border-gray-700 rounded overflow-hidden shadow-md"
                            onClick={() => setSelectedCamera(camera.id)}
                        >
                            <div className="bg-gray-800 text-white p-2 text-center text-sm font-bold tracking-wider">
                                {camera.name}
                            </div>
                            <div className="aspect-video bg-black relative">
                                <HlsPlayer streamUrl={`/api/cameras/live/${camera.id}/stream.m3u8`} />
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};

export default LiveView;