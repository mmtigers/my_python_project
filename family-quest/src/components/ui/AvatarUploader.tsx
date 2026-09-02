import React, { useState, useRef } from "react";
import { Camera } from "lucide-react";
import { apiClient } from "@/lib/apiClient";
import { User } from "@/types";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { isSameOriginAvatarPath } from "@/lib/utils";

interface AvatarUploaderProps {
    user: User;
    onClose: () => void;
    onUploadComplete: () => void;
}

// M15/Issue #325: バックエンド(MY_HOME_SYSTEM/config.py の UPLOAD_MAX_FILE_SIZE_MB)と
// 同じ5MBに揃えている。変更時は両方(とエラー文言)を更新すること。
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
        formData.append('file', fileInputRef.current.files[0]);

        try {
            // ★バグ修正: 以前は存在しない /api/quest/upload_avatar にPOSTしており
            // 常に失敗していた(実際のアップロード先は /api/quest/upload、フィールド名は file)。
            // さらにアップロードするだけではユーザーのアバターには反映されないため、
            // 返ってきたURLを /api/quest/user/update で明示的に紐付ける。
            const { url } = await apiClient.postForm<{ url: string }>('/api/quest/upload', formData);
            await apiClient.post('/api/quest/user/update', { user_id: user.user_id, avatar_url: url });

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
                    {preview || isSameOriginAvatarPath(user.avatar) ? (
                        <img
                            // ★バグ修正: user.avatar はアップロード画像のパス('/uploads/...')の場合と、
                            // 未設定時の絵文字デフォルト値('⚔️'等)の場合がある。preview(選択直後の
                            // data:URLプレビュー)以外は、isSameOriginAvatarPathでパス形式かどうかを
                            // 判定してから<img src>に渡さないと、絵文字を渡した際に壊れた画像アイコンに
                            // なってしまう(UserStatusCard.tsx/Header.tsxと同じ判定に合わせる)。
                            src={preview || user.avatar}
                            alt="Avatar"
                            className="w-full h-full object-cover transition-opacity group-hover:opacity-50"
                        />
                    ) : (
                        <div className="w-full h-full flex items-center justify-center text-4xl group-hover:opacity-50">
                            {user.avatar || user.icon || '👤'}
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