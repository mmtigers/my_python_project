import { describe, expect, it } from 'vitest';
import { isCameraRoute } from './routing';

// #472: pathname.includes('/camera') という単純部分一致だと、将来
// '/settings/camera-help' のような無関係なパスにも誤って一致してしまう。
// セグメント単位の厳密な判定に変更したことの回帰テスト。

describe('isCameraRoute (#472)', () => {
    it.each([
        '/camera',
        '/camera/',
        '/camera/live/cam1',
        '/quest/camera',
        '/quest/camera/',
        '/quest/camera/history',
    ])('treats %s as a camera route', (pathname) => {
        expect(isCameraRoute(pathname)).toBe(true);
    });

    it.each([
        '/',
        '/quest',
        '/quest/',
        '/quest/settings',
        '/settings/camera-help',
        '/camera-settings',
        '/quest/camera-settings',
    ])('does not treat %s as a camera route', (pathname) => {
        expect(isCameraRoute(pathname)).toBe(false);
    });
});
