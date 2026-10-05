import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
    TAP_BACKOFF_BASE_MS, TAP_FLUSH_INTERVAL_MS, TAP_FLUSH_THRESHOLD, TAP_MAX_BATCH,
    TAP_STORAGE_KEY, TAP_STORAGE_MAX, TapLogger, describeTarget,
    type SendResult, type TapBatch, type TapEvent, type TapLoggerDeps,
} from './tapLogger';

function el(html: string): HTMLElement {
    const host = document.createElement('div');
    host.innerHTML = html;
    document.body.appendChild(host);
    return host;
}

describe('describeTarget', () => {
    afterEach(() => { document.body.innerHTML = ''; });

    it('uses the button text of the nearest interactive ancestor', () => {
        const host = el('<button><span id="t">  宿題と明日の準備  </span></button>');
        expect(describeTarget(host.querySelector('#t'))).toEqual({
            element_id: '宿題と明日の準備', element_tag: 'button', is_interactive: true,
        });
    });

    it('prefers data-track, then aria-label, over the visible text', () => {
        const a = el('<button data-track="quest-card" aria-label="x">テキスト</button>').firstElementChild;
        const b = el('<button aria-label="閉じる">×</button>').firstElementChild;
        expect(describeTarget(a).element_id).toBe('quest-card');
        expect(describeTarget(b).element_id).toBe('閉じる');
    });

    it('truncates long labels to 30 characters', () => {
        const long = 'あ'.repeat(50);
        const btn = el(`<button>${long}</button>`).firstElementChild;
        expect(describeTarget(btn).element_id).toBe('あ'.repeat(30));
    });

    it('never records the value of an input', () => {
        const host = el('<input name="reason" value="ひみつ">');
        const input = host.firstElementChild as HTMLInputElement;
        input.value = '個人的な入力内容';
        const d = describeTarget(input);
        expect(d.element_id).toBe('reason');
        expect(JSON.stringify(d)).not.toContain('個人的');
        expect(JSON.stringify(d)).not.toContain('ひみつ');
    });

    it('falls back to name/placeholder/type for unnamed inputs', () => {
        const host = el('<input placeholder="理由を入力">');
        expect(describeTarget(host.firstElementChild).element_id).toBe('理由を入力');
    });

    it('marks a tap on a non-interactive area as not interactive, without an id', () => {
        const host = el('<div><p id="p">ただの文章</p></div>');
        expect(describeTarget(host.querySelector('#p'))).toEqual({
            element_id: null, element_tag: 'p', is_interactive: false,
        });
    });

    it('handles a null target', () => {
        expect(describeTarget(null)).toEqual({ element_id: null, element_tag: null, is_interactive: false });
    });
});

function makeDeps(over: Partial<TapLoggerDeps> = {}) {
    let n = 0;
    let t = 1_000_000;
    const sent: TapBatch[] = [];
    const store = new Map<string, string>();
    const deps: TapLoggerDeps = {
        getContext: () => ({ userId: 'son', screen: 'quest', layoutMode: 'portrait' }),
        send: vi.fn(async (b: TapBatch): Promise<SendResult> => { sent.push(b); return 'ok'; }),
        beacon: vi.fn(() => true),
        storage: {
            getItem: (k: string) => store.get(k) ?? null,
            setItem: (k: string, v: string) => { store.set(k, v); },
            removeItem: (k: string) => { store.delete(k); },
        },
        now: () => t,
        uuid: () => `uuid-${++n}`,
        ...over,
    };
    return { deps, sent, store, advance: (ms: number) => { t += ms; } };
}

function ev(i: number): TapEvent {
    return {
        event_id: `e-${i}`, occurred_at_ms: 1000 + i, user_id: 'son', screen: 'quest',
        element_id: 'btn', element_tag: 'button', is_interactive: true, x_pct: 1, y_pct: 2, layout_mode: 'portrait',
    };
}

describe('TapLogger batching', () => {
    it('does not send until the threshold is reached', () => {
        const { deps, sent } = makeDeps();
        const logger = new TapLogger(deps);
        for (let i = 0; i < TAP_FLUSH_THRESHOLD - 1; i++) logger.enqueue(ev(i));
        expect(sent).toHaveLength(0);
        expect(logger.pending).toHaveLength(TAP_FLUSH_THRESHOLD - 1);
    });

    it('sends one batch when the threshold is reached and empties the queue', async () => {
        const { deps, sent } = makeDeps();
        const logger = new TapLogger(deps);
        for (let i = 0; i < TAP_FLUSH_THRESHOLD; i++) logger.enqueue(ev(i));
        await vi.waitFor(() => expect(sent).toHaveLength(1));
        expect(sent[0].events).toHaveLength(TAP_FLUSH_THRESHOLD);
        expect(sent[0].session_id).toBe('uuid-1');
        expect(logger.pending).toHaveLength(0);
    });

    it('caps a request at 100 events and continues with the rest', async () => {
        const { deps, sent } = makeDeps();
        const logger = new TapLogger(deps);
        // しきい値に達するたびに flush が走るため、先に送信中にして一度にためる
        (logger as unknown as { sending: boolean }).sending = true;
        for (let i = 0; i < 250; i++) logger.enqueue(ev(i));
        (logger as unknown as { sending: boolean }).sending = false;
        await logger.flush();
        await vi.waitFor(() => expect(logger.pending).toHaveLength(0));
        expect(sent.map(b => b.events.length)).toEqual([TAP_MAX_BATCH, TAP_MAX_BATCH, 50]);
    });

    it('keeps taps enqueued during an in-flight send', async () => {
        let release: (r: SendResult) => void = () => {};
        const { deps } = makeDeps({ send: () => new Promise<SendResult>(r => { release = r; }) });
        const logger = new TapLogger(deps);
        logger.enqueue(ev(0));
        const p = logger.flush();
        logger.enqueue(ev(1));
        release('ok');
        await p;
        expect(logger.pending.map(e => e.event_id)).toEqual(['e-1']);
    });
});

describe('TapLogger failure handling', () => {
    it('keeps the queue, persists it and backs off after a failed send', async () => {
        const { deps, store, advance } = makeDeps({ send: vi.fn(async () => 'retry' as SendResult) });
        const logger = new TapLogger(deps);
        logger.enqueue(ev(0));
        await logger.flush();
        expect(logger.pending).toHaveLength(1);
        expect(JSON.parse(store.get(TAP_STORAGE_KEY)!)).toHaveLength(1);

        await logger.flush(); // バックオフ中は送らない
        expect(deps.send).toHaveBeenCalledTimes(1);

        advance(TAP_BACKOFF_BASE_MS + 1);
        await logger.flush();
        expect(deps.send).toHaveBeenCalledTimes(2);
        // 2回目の失敗では待ち時間が倍になる
        advance(TAP_BACKOFF_BASE_MS + 1);
        await logger.flush();
        expect(deps.send).toHaveBeenCalledTimes(2);
    });

    it('treats a thrown send as a retry', async () => {
        const { deps } = makeDeps({ send: vi.fn(async () => { throw new Error('boom'); }) });
        const logger = new TapLogger(deps);
        logger.enqueue(ev(0));
        await expect(logger.flush()).resolves.toBeUndefined();
        expect(logger.pending).toHaveLength(1);
    });

    it('drops a batch the server will never accept (4xx)', async () => {
        const { deps, store } = makeDeps({ send: vi.fn(async () => 'drop' as SendResult) });
        const logger = new TapLogger(deps);
        logger.enqueue(ev(0));
        await logger.flush();
        expect(logger.pending).toHaveLength(0);
        expect(store.has(TAP_STORAGE_KEY)).toBe(false);
    });

    it('recovers and clears the persisted queue after a later success', async () => {
        let ok = false;
        const { deps, store, advance } = makeDeps({ send: vi.fn(async () => (ok ? 'ok' : 'retry') as SendResult) });
        const logger = new TapLogger(deps);
        logger.enqueue(ev(0));
        await logger.flush();
        ok = true;
        advance(TAP_BACKOFF_BASE_MS + 1);
        await logger.flush();
        expect(logger.pending).toHaveLength(0);
        expect(store.has(TAP_STORAGE_KEY)).toBe(false);
    });

    it('drops the oldest taps beyond the storage cap', () => {
        const { deps } = makeDeps({ send: vi.fn(async () => 'retry' as SendResult) });
        const logger = new TapLogger(deps);
        (logger as unknown as { sending: boolean }).sending = true;
        for (let i = 0; i < TAP_STORAGE_MAX + 10; i++) logger.enqueue(ev(i));
        expect(logger.pending).toHaveLength(TAP_STORAGE_MAX);
        expect(logger.pending[0].event_id).toBe('e-10');
    });
});

describe('TapLogger page hide / restore', () => {
    it('sends via beacon on hide and removes the beaconed taps', () => {
        const { deps } = makeDeps();
        const logger = new TapLogger(deps);
        logger.enqueue(ev(0));
        logger.enqueue(ev(1));
        logger.flushOnHide();
        expect(deps.beacon).toHaveBeenCalledTimes(1);
        expect(logger.pending).toHaveLength(0);
    });

    it('keeps and persists taps when the beacon is refused', () => {
        const { deps, store } = makeDeps({ beacon: vi.fn(() => false) });
        const logger = new TapLogger(deps);
        logger.enqueue(ev(0));
        logger.flushOnHide();
        expect(logger.pending).toHaveLength(1);
        expect(JSON.parse(store.get(TAP_STORAGE_KEY)!)).toHaveLength(1);
    });

    it('restores unsent taps from storage without duplicating existing ones', () => {
        const { deps, store } = makeDeps();
        store.set(TAP_STORAGE_KEY, JSON.stringify([ev(1), ev(2)]));
        const logger = new TapLogger(deps);
        logger.enqueue(ev(2));
        logger.restore();
        expect(logger.pending.map(e => e.event_id)).toEqual(['e-1', 'e-2']);
    });

    it('ignores corrupted storage', () => {
        const { deps, store } = makeDeps();
        store.set(TAP_STORAGE_KEY, '{not json');
        const logger = new TapLogger(deps);
        expect(() => logger.restore()).not.toThrow();
        expect(logger.pending).toHaveLength(0);
        store.set(TAP_STORAGE_KEY, JSON.stringify([{ nope: 1 }, 'x', null]));
        logger.restore();
        expect(logger.pending).toHaveLength(0);
    });

    it('works without storage', async () => {
        const { deps } = makeDeps({ storage: null });
        const logger = new TapLogger(deps);
        logger.enqueue(ev(0));
        logger.flushOnHide();
        logger.restore();
        expect(logger.pending).toHaveLength(0);
    });
});

describe('TapLogger DOM wiring (start)', () => {
    let stop: (() => void) | null = null;
    beforeEach(() => { vi.useFakeTimers(); });
    afterEach(() => {
        stop?.();
        stop = null;
        vi.useRealTimers();
        document.body.innerHTML = '';
    });

    function tap(target: Element, init: PointerEventInit = {}) {
        // jsdom は PointerEvent を持たない版があるため、MouseEvent 由来で pointerdown を作る
        const e = new MouseEvent('pointerdown', { bubbles: true, clientX: 512, clientY: 384, ...init });
        Object.defineProperty(e, 'isPrimary', { value: init.isPrimary ?? true });
        Object.defineProperty(e, 'pointerType', { value: init.pointerType ?? 'touch' });
        target.dispatchEvent(e);
    }

    it('records a tap with context, position and interactivity', () => {
        const host = el('<button id="b">ごほうび</button><p id="p">文章</p>');
        const { deps } = makeDeps();
        const logger = new TapLogger(deps);
        stop = logger.start();
        Object.defineProperty(window, 'innerWidth', { value: 1024, configurable: true });
        Object.defineProperty(window, 'innerHeight', { value: 768, configurable: true });

        tap(host.querySelector('#b')!);
        tap(host.querySelector('#p')!);
        expect(logger.pending).toHaveLength(2);
        expect(logger.pending[0]).toMatchObject({
            user_id: 'son', screen: 'quest', element_id: 'ごほうび', element_tag: 'button',
            is_interactive: true, x_pct: 50, y_pct: 50, layout_mode: 'portrait',
        });
        expect(logger.pending[1]).toMatchObject({ element_id: null, element_tag: 'p', is_interactive: false });
    });

    it('truncates fields to the backend limits so one bad tap cannot get a whole batch rejected', () => {
        const host = el(`<x-${'a'.repeat(30)} data-tap-user="${'u'.repeat(80)}" data-tap-screen="${'s'.repeat(50)}"><button id="b">x</button></x-${'a'.repeat(30)}>`);
        const logger = new TapLogger(makeDeps({ getContext: () => ({ userId: null, screen: 'quest', layoutMode: 'l'.repeat(40) }) }).deps);
        stop = logger.start();
        tap(host.querySelector('#b')!);
        const e = logger.pending[0];
        expect(e.user_id).toHaveLength(64);
        expect(e.screen).toHaveLength(32);
        expect(e.layout_mode).toHaveLength(16);
        expect(e.element_tag!.length).toBeLessThanOrEqual(16);

        const dead = el(`<x-${'b'.repeat(30)}>text</x-${'b'.repeat(30)}>`);
        tap(dead.firstElementChild!);
        expect(logger.pending[1].element_tag!.length).toBeLessThanOrEqual(16);
    });

    it('lets a panel override the user and screen', () => {
        const host = el('<div data-tap-user="mom" data-tap-screen="shop"><button id="b">買う</button></div>');
        const logger = new TapLogger(makeDeps().deps);
        stop = logger.start();
        tap(host.querySelector('#b')!);
        expect(logger.pending[0]).toMatchObject({ user_id: 'mom', screen: 'shop' });
    });

    it('ignores non-primary pointers and non-left mouse buttons', () => {
        const host = el('<button id="b">x</button>');
        const logger = new TapLogger(makeDeps().deps);
        stop = logger.start();
        tap(host.querySelector('#b')!, { isPrimary: false });
        tap(host.querySelector('#b')!, { pointerType: 'mouse', button: 2 });
        expect(logger.pending).toHaveLength(0);
    });

    it('flushes on the interval and when the page is hidden', async () => {
        const host = el('<button id="b">x</button>');
        const { deps, sent } = makeDeps();
        const logger = new TapLogger(deps);
        stop = logger.start();
        tap(host.querySelector('#b')!);
        await vi.advanceTimersByTimeAsync(TAP_FLUSH_INTERVAL_MS);
        expect(sent).toHaveLength(1);

        tap(host.querySelector('#b')!);
        Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true });
        document.dispatchEvent(new Event('visibilitychange'));
        expect(deps.beacon).toHaveBeenCalledTimes(1);
        Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
    });

    it('stops recording after the cleanup function runs', () => {
        const host = el('<button id="b">x</button>');
        const logger = new TapLogger(makeDeps().deps);
        const cleanup = logger.start();
        cleanup();
        tap(host.querySelector('#b')!);
        expect(logger.pending).toHaveLength(0);
    });
});
