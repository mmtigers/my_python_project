import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import AvatarUploader from './AvatarUploader';
import { apiClient } from '../../lib/apiClient';
import { User } from '../../types';

// #442: AvatarUploader.tsxの2段階アップロード(画像アップロード→ユーザーへの紐付け)の
// うち2段階目が失敗した場合、1段階目でアップロード済みの画像がどのユーザーにも
// 紐付かないまま孤立ファイルとして残っていた問題の回帰テスト。

vi.mock('../../lib/apiClient', () => ({
    apiClient: {
        postForm: vi.fn(),
        post: vi.fn(),
        delete: vi.fn(),
    },
}));

const user: User = { user_id: 'dad', name: 'Dad', level: 1, exp: 0, gold: 0, avatar: '🙂' };

async function selectAFile() {
    const file = new File(['dummy'], 'avatar.png', { type: 'image/png' });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    // 「保存する」ボタンはpreview(FileReaderの非同期読み込み完了)まで無効化されている
    // ため、プレビュー画像が表示されるまで待ってからでないとクリックが効かない。
    await waitFor(() => expect(screen.getByAltText('Avatar')).toBeInTheDocument());
}

describe('AvatarUploader rollback on link failure (#442)', () => {
    afterEach(() => {
        cleanup();
        vi.resetAllMocks();
    });

    it('rolls back (deletes) the uploaded file when linking it to the user fails', async () => {
        vi.mocked(apiClient.postForm).mockResolvedValue({ url: '/uploads/abc123.png' });
        vi.mocked(apiClient.post).mockRejectedValue(new Error('リンクに失敗しました'));
        vi.mocked(apiClient.delete).mockResolvedValue(undefined);

        render(<AvatarUploader user={user} onClose={vi.fn()} onUploadComplete={vi.fn()} />);

        await selectAFile();
        fireEvent.click(screen.getByText('保存する'));

        await waitFor(() => expect(apiClient.delete).toHaveBeenCalledWith('/api/quest/upload/abc123.png'));
        expect(screen.getByText('リンクに失敗しました')).toBeInTheDocument();
    });

    it('does not attempt a rollback when the upload step itself fails', async () => {
        vi.mocked(apiClient.postForm).mockRejectedValue(new Error('アップロードに失敗しました'));

        render(<AvatarUploader user={user} onClose={vi.fn()} onUploadComplete={vi.fn()} />);

        await selectAFile();
        fireEvent.click(screen.getByText('保存する'));

        await waitFor(() => expect(screen.getByText('アップロードに失敗しました')).toBeInTheDocument());
        expect(apiClient.delete).not.toHaveBeenCalled();
    });

    it('does not roll back when both steps succeed', async () => {
        vi.mocked(apiClient.postForm).mockResolvedValue({ url: '/uploads/abc123.png' });
        vi.mocked(apiClient.post).mockResolvedValue({ status: 'updated', avatar: '/uploads/abc123.png' });

        const onUploadComplete = vi.fn();
        render(<AvatarUploader user={user} onClose={vi.fn()} onUploadComplete={onUploadComplete} />);

        await selectAFile();
        fireEvent.click(screen.getByText('保存する'));

        await waitFor(() => expect(onUploadComplete).toHaveBeenCalled());
        expect(apiClient.delete).not.toHaveBeenCalled();
    });
});
