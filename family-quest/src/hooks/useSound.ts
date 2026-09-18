import { useCallback } from 'react';

// #660: 以前は '/quest/...' を直書きしており、vite.config.ts の `base` と二重管理だった
// (base を変えると音だけ 404 になる)。ビルド時に base が埋め込まれる
// import.meta.env.BASE_URL から組み立てる。BASE_URL は必ず末尾 '/' を含む。
const soundUrl = (file: string) => `${import.meta.env.BASE_URL}${file}`;

const SOUNDS = {
    submit: soundUrl('submit.mp3'),       // 申請・決定音
    approve: soundUrl('approve.mp3'),     // 親承認
    clear: soundUrl('quest_clear.mp3'),   // クエスト完了
    levelUp: soundUrl('level_up.mp3'),    // レベルアップ
    medal: soundUrl('medal_get.mp3'),     // メダル獲得
    tap: soundUrl('tap.mp3'),             // タップ音
    select: soundUrl('submit.mp3'),       // select は submit(決定音) を使用
    cancel: soundUrl('tap.mp3'),          // cancel は tap(タップ音) を使用
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