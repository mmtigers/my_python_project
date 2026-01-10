import React, { useState, useRef } from 'react';
import { Upload, X, Loader2, Camera } from 'lucide-react';
import { apiClient } from "../../lib/apiClient";

const AvatarUploader = ({ user, onClose, onUploadComplete }) => {
    const [uploading, setUploading] = useState(false);
    const fileInputRef = useRef(null);

    const handleFileChange = async (e) => {
        const file = e.target.files[0];
        if (!file) return;

        // ★追加: スマホ写真の拡張子チェック (HEIC等はサーバーで弾かれるため)
        const fileName = file.name.toLowerCase();
        if (!fileName.match(/\.(jpg|jpeg|png|gif|webp)$/)) {
            alert("エラー: 対応していないファイル形式です。\nJPG, PNG, GIF形式の画像を選択してください。\n(iPhoneの場合は「互換性優先」設定か、スクリーンショット等を試してください)");
            return;
        }

        setUploading(true);
        try {
            // 1. 画像アップロード
            const formData = new FormData();
            formData.append('file', file);

            // エンドポイントは環境に合わせて調整してください (/api/quest/upload を想定)
            const uploadUrl = `${import.meta.env.VITE_API_URL || ''}/api/quest/upload`;
            const uploadRes = await fetch(uploadUrl, {
                method: 'POST',
                body: formData,
            });

            // ★変更: エラー時の詳細を取得して表示
            if (!uploadRes.ok) {
                const errorData = await uploadRes.json().catch(() => ({}));
                throw new Error(errorData.detail || `Upload Error: ${uploadRes.status}`);
            }

            if (!uploadRes.ok) throw new Error('Upload failed');
            const data = await uploadRes.json();
            const imageUrl = data.url; // サーバーから返却されるパス (例: /uploads/xxx.jpg)

            // 2. ユーザーアバター情報の更新
            await apiClient.post('api/quest/user/update', {
                user_id: user.user_id,
                avatar_url: imageUrl
            });

            // 3. 完了通知
            onUploadComplete();
            onClose();

        } catch (error) {
            console.error(error);
            // ★変更: 具体的なエラー内容をアラート表示
            alert(`画像のアップロードに失敗しました。\n詳細: ${error.message}`);
        } finally {
            setUploading(false);
        }
    };

    return (
        <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4 animate-in fade-in">
            <div className="bg-slate-800 rounded-lg p-6 w-full max-w-sm border border-slate-600 shadow-2xl relative">
                <button onClick={onClose} className="absolute top-2 right-2 text-slate-400 hover:text-white">
                    <X size={24} />
                </button>

                <h3 className="text-xl font-bold text-white mb-4 text-center">アバター変更</h3>

                <div className="flex flex-col items-center gap-6">
                    {/* 現在のアバタープレビュー */}
                    <div className="w-24 h-24 bg-slate-700 rounded-full flex items-center justify-center border-2 border-dashed border-slate-500 overflow-hidden relative">
                        {user.avatar && (user.avatar.startsWith('/uploads') || user.avatar.startsWith('http')) ? (
                            <img src={user.avatar} alt="current" className="w-full h-full object-cover opacity-80" />
                        ) : (
                            <span className="text-4xl grayscale opacity-50">{user.avatar}</span>
                        )}
                    </div>

                    {uploading ? (
                        <div className="flex flex-col items-center text-blue-300">
                            <Loader2 className="animate-spin mb-2" size={32} />
                            <span>アップロード中...</span>
                        </div>
                    ) : (
                        <div className="w-full space-y-3">
                            <button
                                onClick={() => fileInputRef.current.click()}
                                className="w-full py-3 bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-bold flex items-center justify-center gap-2 shadow-lg transition-transform active:scale-95"
                            >
                                <Camera size={20} />
                                写真を撮る / 選ぶ
                            </button>
                            <input
                                type="file"
                                ref={fileInputRef}
                                onChange={handleFileChange}
                                accept="image/*"
                                className="hidden"
                            />
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

export default AvatarUploader;