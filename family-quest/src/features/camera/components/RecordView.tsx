import React, { useState, useRef } from 'react';
import HlsPlayer from '../../../components/ui/HlsPlayer';
import { CameraConfig } from '../types';

interface RecordViewProps {
    cameras: CameraConfig[];
}

const RecordView: React.FC<RecordViewProps> = ({ cameras }) => {
    const [targetDate, setTargetDate] = useState<string>('');
    const [targetTime, setTargetTime] = useState<string>('');
    const [playUrlSuffix, setPlayUrlSuffix] = useState<string | null>(null);
    const [startOffsets, setStartOffsets] = useState<{ [key: string]: number }>({});
    const timeInputRef = useRef<HTMLInputElement>(null); // ★追加: input要素への参照用

    const videoRefs = useRef<{ [key: string]: HTMLVideoElement | null }>({});

    const handlePlay = async () => {
        if (!targetDate || !targetTime) {
            alert("日付と時刻を指定してください");
            return;
        }

        const dateStr = targetDate.replace(/-/g, '');
        const [hours, minutes] = targetTime.split(':').map(Number);
        const totalSeconds = hours * 3600 + minutes * 60;

        // 各カメラごとにAPIからオフセットを取得し、正確なシーク位置を計算する
        const offsets: { [key: string]: number } = {};
        for (const camera of cameras) {
            try {
                const res = await fetch(`/api/cameras/record/${camera.id}/${dateStr}/info`);
                if (res.ok) {
                    const data = await res.json();
                    // (指定時刻の総秒数) - (その日の最初のファイルの開始秒数)
                    offsets[camera.id] = Math.max(0, totalSeconds - data.offset_seconds);
                } else {
                    offsets[camera.id] = totalSeconds;
                }
            } catch (err) {
                console.error("Failed to fetch offset", err);
                offsets[camera.id] = totalSeconds;
            }
        }

        setStartOffsets(offsets);
        // バックエンドが生成するファイル名 (record_YYYYMMDD.m3u8) と一致させる
        setPlayUrlSuffix(`${dateStr}/record_${dateStr}.m3u8`);
    };

    const handleGlobalPlay = () => Object.values(videoRefs.current).forEach(v => v?.play());
    const handleGlobalPause = () => Object.values(videoRefs.current).forEach(v => v?.pause());
    const handleGlobalRateChange = (rate: number) => {
        Object.values(videoRefs.current).forEach(v => {
            if (v) v.playbackRate = rate;
        });
    };

    return (
        <div className="w-full">
            <div className="bg-gray-200 p-4 rounded-lg mb-4 flex flex-wrap gap-4 items-end shadow">
                <div>
                    <label className="block text-sm font-bold text-gray-700 mb-1">日付</label>
                    {/* 背景色と文字色を明示的に指定して視認性を改善 */}
                    <input type="date" className="p-2 border rounded bg-white text-gray-900" value={targetDate} onChange={e => setTargetDate(e.target.value)} />
                </div>
                <div>
                    <label className="block text-sm font-bold text-gray-700 mb-1">時刻</label>
                    {/* 背景色と文字色を明示的に指定して視認性を改善 */}
                    <input
                        type="time"
                        ref={timeInputRef} // ★追加
                        className="p-2 border rounded bg-white text-gray-900"
                        value={targetTime}
                        onChange={e => {
                            setTargetTime(e.target.value);
                            // ★追加: 値が入力されたらフォーカスを外し、プルダウンを強制的に閉じる
                            if (e.target.value) {
                                timeInputRef.current?.blur();
                            }
                        }}
                    />
                </div>
                <button className="px-8 py-2 bg-green-600 hover:bg-green-700 text-white rounded font-bold transition-colors" onClick={handlePlay}>
                    再生開始
                </button>
            </div>

            {playUrlSuffix && (
                <div className="flex flex-wrap gap-2 mb-4 justify-center bg-gray-800 p-2 rounded">
                    <button className="px-4 py-2 bg-blue-500 text-white rounded" onClick={handleGlobalPlay}>▶ 同期再生</button>
                    <button className="px-4 py-2 bg-orange-500 text-white rounded" onClick={handleGlobalPause}>⏸ 同期一時停止</button>
                    <div className="w-px bg-gray-500 mx-2"></div>
                    <button className="px-4 py-2 bg-gray-600 text-white rounded" onClick={() => handleGlobalRateChange(1)}>1x</button>
                    <button className="px-4 py-2 bg-gray-600 text-white rounded" onClick={() => handleGlobalRateChange(2)}>2x</button>
                    <button className="px-4 py-2 bg-gray-600 text-white rounded" onClick={() => handleGlobalRateChange(4)}>4x</button>
                </div>
            )}

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {cameras.map(camera => (
                    <div key={camera.id} className="border border-gray-700 rounded overflow-hidden shadow-md">
                        <div className="bg-gray-800 text-white p-1 text-center text-xs tracking-wider">{camera.name}</div>
                        <div className="aspect-video bg-black relative">
                            {playUrlSuffix ? (
                                <HlsPlayer
                                    streamUrl={`/api/cameras/record/${camera.id}/${playUrlSuffix}`}
                                    autoPlay={true}
                                    muted={true}
                                    startPosition={startOffsets[camera.id] || 0}
                                    onVideoRef={(el) => { videoRefs.current[camera.id] = el; }}
                                />
                            ) : (
                                <div className="absolute inset-0 flex items-center justify-center text-gray-600">待機中</div>
                            )}
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
};

export default RecordView;