import { describe, expect, it } from 'vitest';
import { recordPositionForTime } from './recordTimeline';

// 10:00:00 から 600 秒、10:20:00 から 300 秒(10:10〜10:20 は録画なし)
const info = {
    offset_seconds: 36000,
    files: [
        { start_seconds: 36000, position: 0, duration: 600 },
        { start_seconds: 37200, position: 600, duration: 300 },
    ],
};

describe('recordPositionForTime', () => {
    it('maps a time inside a recording to its position in the playlist', () => {
        expect(recordPositionForTime(info, 36000 + 90)).toBe(90);
        expect(recordPositionForTime(info, 37200 + 30)).toBe(630);
    });

    it('jumps to the next recording when the time falls into a gap', () => {
        expect(recordPositionForTime(info, 36000 + 700)).toBe(600);
    });

    it('starts from the first recording when the time is before it', () => {
        expect(recordPositionForTime(info, 3600)).toBe(0);
    });

    it('starts near the end when the time is after the last recording', () => {
        expect(recordPositionForTime(info, 80000)).toBe(895);
    });

    it('falls back to the offset from the first file without a timeline', () => {
        expect(recordPositionForTime({ offset_seconds: 36000 }, 36000 + 120)).toBe(120);
        expect(recordPositionForTime({ offset_seconds: 36000, files: [] }, 100)).toBe(0);
    });
});
