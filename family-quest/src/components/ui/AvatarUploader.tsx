import React, { useState, useRef } from "react";
import { Camera } from "lucide-react";
import { apiClient } from "@/lib/apiClient";
import { User } from "@/types";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";

interface AvatarUploaderProps {
    user: User;
    onClose: () => void;
    onUploadComplete: () => void;
}

const MAX_AVATAR_SIZE_BYTES = 5 * 1024 * 1024; // 5MB

const AvatarUploader: React.FC<AvatarUploaderProps> = ({ user, onClose, onUploadComplete }) => {
    const [uploading, setUploading] = useState(false);
    const [preview, setPreview] = useState<string | null>(null);
    const [errorMessage, setErrorMessage] = useState<string | null>(null);
    const [uploadDone, setUploadDone] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;

        setErrorMessage(null);

        // クライアント側バリデーション: 画像ファイルかつサイズ上限以下であることを確認
        if (!file.type.startsWith('image/')) {
            setErrorMessage("画像ファイルを選択してください");
            e.target.value = '';
            setPreview(null);
            return;
        }
        if (file.size > MAX_AVATAR_SIZE_BYTES) {
            setErrorMessage("ファイルサイズが大きすぎます（5MBまで）");
            e.target.value = '';
            setPreview(null);
            return;
        }

        // プレビュー表示
        const reader = new FileReader();
        reader.onloadend = () => {
            setPreview(reader.result as string);
        };
        reader.readAsDataURL(file);
    };

    const handleUpload = async () => {
        if (!fileInputRef.current?.files?.[0]) return;

        setUploading(true);
        setErrorMessage(null);
        const formData = new FormData();
        formData.append('avatar', fileInputRef.current.files[0]);
        formData.append('user_id', user.user_id);

        try {
            await apiClient.postForm('/api/quest/upload_avatar', formData);
            // ★変更: 素の alert() を廃止し、アプリ標準のモーダル内メッセージで完了を伝える。
            // データ更新自体は即座に行い、モーダルはユーザーが「閉じる」を押すまで残す。
            onUploadComplete();
            setUploadDone(true);
        } catch (error) {
            console.error('Upload failed:', error);
            setErrorMessage(error instanceof Error ? error.message : "アップロードに失敗しました");
        } finally {
            setUploading(false);
        }
    };

    const triggerSelect = () => {
        fileInputRef.current?.click();
    };

    return (
        <Modal isOpen={true} onClose={onClose} title="アバター変更">
            <div className="flex flex-col items-center gap-6">
                {/* プレビューエリア */}
                <div
                    className="w-32 h-32 rounded-full border-4 border-slate-600 bg-slate-800 overflow-hidden relative cursor-pointer group shadow-xl"
                    onClick={triggerSelect}
                >
                    {preview || user.avatar ? (
                        <img
                            src={preview || user.avatar}
                            alt="Avatar"
                            className="w-full h-full object-cover transition-opacity group-hover:opacity-50"
                        />
                    ) : (
                        <div className="w-full h-full flex items-center justify-center text-4xl group-hover:opacity-50">
                            {user.icon || '👤'}
                        </div>
                    )}

                    <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-all">
                        <Camera size={32} className="text-white drop-shadow-md" />
                    </div>
                </div>

                <input
                    type="file"
                    ref={fileInputRef}
                    onChange={handleFileChange}
                    accept="image/*"
                    className="hidden"
                />

                <div className="text-sm text-gray-400 text-center">
                    クリックして画像を選択<br />
                    (正方形にトリミングされます)
                </div>

                {errorMessage && (
                    <div className="text-sm text-red-400 text-center bg-red-950/40 border border-red-700 rounded px-3 py-2 w-full">
                        {errorMessage}
                    </div>
                )}

                {uploadDone && (
                    <div className="text-sm text-green-400 text-center bg-green-950/40 border border-green-700 rounded px-3 py-2 w-full">
                        アバターを変更しました！
                    </div>
                )}

                <div className="flex gap-4 w-full">
                    {uploadDone ? (
                        <Button variant="primary" onClick={onClose} className="flex-1">
                            閉じる
                        </Button>
                    ) : (
                        <>
                            <Button variant="secondary" onClick={onClose} className="flex-1" disabled={uploading}>
                                キャンセル
                            </Button>
                            <Button
                                variant="primary"
                                onClick={handleUpload}
                                className="flex-1"
                                disabled={!preview && !fileInputRef.current?.files?.[0]}
                                isLoading={uploading}
                            >
                                保存する
                            </Button>
                        </>
                    )}
                </div>
            </div>
        </Modal>
    );
};

export default AvatarUploader;