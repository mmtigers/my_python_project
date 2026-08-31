import { describe, expect, it } from 'vitest';
import { cn, isSameOriginAvatarPath } from './utils';

describe('cn', () => {
    it('merges class names and drops conflicting Tailwind utilities', () => {
        const isTrue: boolean = true;
        expect(cn('bg-red-500', isTrue && 'p-4', 'p-2')).toBe('bg-red-500 p-2');
    });

    it('drops falsy values', () => {
        const isFalse: boolean = false;
        expect(cn('a', isFalse && 'b', undefined, null, 'c')).toBe('a c');
    });
});

describe('isSameOriginAvatarPath', () => {
    it('accepts a same-origin relative path', () => {
        expect(isSameOriginAvatarPath('/uploads/avatar123.png')).toBe(true);
    });

    it('rejects a protocol-relative URL (M-9-5)', () => {
        expect(isSameOriginAvatarPath('//evil.example/x.png')).toBe(false);
    });

    it('rejects an absolute external URL', () => {
        expect(isSameOriginAvatarPath('https://evil.example/x.png')).toBe(false);
    });

    it('rejects a path without a leading slash', () => {
        expect(isSameOriginAvatarPath('uploads/avatar123.png')).toBe(false);
    });

    it('rejects undefined, null, and empty string', () => {
        expect(isSameOriginAvatarPath(undefined)).toBe(false);
        expect(isSameOriginAvatarPath(null)).toBe(false);
        expect(isSameOriginAvatarPath('')).toBe(false);
    });
});
