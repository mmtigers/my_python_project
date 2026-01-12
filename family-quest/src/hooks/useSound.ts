import { useCallback } from 'react';

// ★修正: すべてのパスの先頭に '/quest' を追加しました
const SOUNDS = {
    submit: '/quest/submit.mp3',       // 申請・決定音
    approve: '/quest/approve.mp3',     // 親承認
    clear: '/quest/quest_clear.mp3',   // クエスト完了
    levelUp: '/quest/level_up.mp3',    // レベルアップ
    medal: '/quest/medal_get.mp3',     // メダル獲得
    tap: '/quest/tap.mp3',             // タップ音
} as const;

type SoundKey = keyof typeof SOUNDS;

// Audioオブジェクトをキャッシュ
const audioCache: Partial<Record<SoundKey, HTMLAudioElement>> = {};

export const useSound = () => {
    const play = useCallback((key: SoundKey) => {
        try {
            const path = SOUNDS[key];

            // キャッシュになければ作成
            if (!audioCache[key]) {
                audioCache[key] = new Audio(path);
            }

            const audio = audioCache[key];
            if (audio) {
                audio.currentTime = 0; // 連続再生用にリセット

                // タップ音は少し音量を下げる
                if (key === 'tap') audio.volume = 0.5;

                // 再生
                audio.play().catch(e => {
                    // ユーザー操作直後でない場合など、ブラウザがブロックした場合は警告だけ出す
                    console.warn('Sound play failed:', e);
                });
            }
        } catch (error) {
            console.error('Audio setup error:', error);
        }
    }, []);

    return { play };
};