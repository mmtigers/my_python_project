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


// 保存ボタンの有効/無効は「検証を通ったファイルが選ばれているか」で決める。
// 以前は描画中に fileInputRef.current.files を読んでいたが、ref は変わっても再描画を
// 起こさないため値が古くなりうる(eslint-plugin-react-hooks 7 の refs ルール)。
describe('AvatarUploader save button follows the selected file', () => {
    afterEach(() => {
        cleanup();
        vi.resetAllMocks();
    });

    const saveButton = () => screen.getByText('保存する').closest('button') as HTMLButtonElement;
    const input = () => document.querySelector('input[type="file"]') as HTMLInputElement;

    it('is disabled until a file is chosen', () => {
        render(<AvatarUploader user={user} onClose={vi.fn()} onUploadComplete={vi.fn()} />);
        expect(saveButton()).toBeDisabled();
    });

    it('is enabled as soon as a valid image is chosen, without waiting for the preview', () => {
        // FileReader を止め、プレビューが永久に来ない状況でも有効になることを確かめる
        const readSpy = vi.spyOn(FileReader.prototype, 'readAsDataURL').mockImplementation(() => {});
        render(<AvatarUploader user={user} onClose={vi.fn()} onUploadComplete={vi.fn()} />);
        fireEvent.change(input(), { target: { files: [new File(['x'], 'a.png', { type: 'image/png' })] } });
        expect(saveButton()).toBeEnabled();
        readSpy.mockRestore();
    });

    it('goes back to disabled when an invalid file replaces a valid one', async () => {
        render(<AvatarUploader user={user} onClose={vi.fn()} onUploadComplete={vi.fn()} />);
        fireEvent.change(input(), { target: { files: [new File(['x'], 'a.png', { type: 'image/png' })] } });
        expect(saveButton()).toBeEnabled();
        fireEvent.change(input(), { target: { files: [new File(['x'], 'a.txt', { type: 'text/plain' })] } });
        expect(saveButton()).toBeDisabled();
        expect(screen.getByText('画像ファイルを選択してください')).toBeInTheDocument();
    });

    it('uploads the chosen file itself', async () => {
        vi.mocked(apiClient.postForm).mockResolvedValue({ url: '/uploads/x.png' });
        vi.mocked(apiClient.post).mockResolvedValue({});
        render(<AvatarUploader user={user} onClose={vi.fn()} onUploadComplete={vi.fn()} />);
        const file = new File(['x'], 'mine.png', { type: 'image/png' });
        fireEvent.change(input(), { target: { files: [file] } });
        fireEvent.click(saveButton());
        await waitFor(() => expect(apiClient.postForm).toHaveBeenCalled());
        const form = vi.mocked(apiClient.postForm).mock.calls[0][1] as FormData;
        expect(form.get('file')).toBe(file);
    });
});
