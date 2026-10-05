// family-quest/src/lib/tapLogger.ts
//
// UI/UX改善のための画面タップログ。「いつ・どの画面の・どの要素を」タップしたかを
// 記録し、バックエンド(POST /api/ui-log/events → ui_tap_events)へバッチで送る。
//
// 設計(1タップごとにPOSTしない理由は、リクエスト過多とラズパイのSDカードへの書き込みを
// 避けるため):
//   - `document` へ pointerdown を capture 段階で1回だけ登録する(各ボタンへの記述は不要)。
//   - タップはメモリのキューへためる。20件たまるか30秒ごと、および画面を隠す/閉じる
//     ときに、まとめて(1回最大100件)送る。UI を止めない(非同期・失敗は握りつぶす)。
//   - 送信に失敗したら指数バックオフで再試行し、キューは localStorage にも退避する
//     (オフライン復帰やリロード後に再送する。上限 TAP_STORAGE_MAX 件、超えたら古い順に捨てる)。
//   - 各タップに event_id(UUID)を付け、再送しても二重に記録されないようにする(サーバー側で冪等)。
//   - 入力欄の値は記録しない(要素の識別には name/placeholder/種類だけを使う)。
//
// カメラ画面(/camera)は main.tsx で App とは別にマウントされるため、この仕組みは動かない。
import { apiClient } from './apiClient';

export const TAP_LOG_ENDPOINT = '/api/ui-log/events';
export const TAP_FLUSH_THRESHOLD = 20;
export const TAP_FLUSH_INTERVAL_MS = 30_000;
/** 1リクエストの上限。バックエンド config.UI_TAP_LOG_MAX_BATCH と揃える。 */
export const TAP_MAX_BATCH = 100;
export const TAP_STORAGE_KEY = 'familyQuest.tapLog.pending.v1';
export const TAP_STORAGE_MAX = 500;
export const TAP_BACKOFF_BASE_MS = 5_000;
export const TAP_BACKOFF_MAX_MS = 300_000;
/** 画面を隠す/閉じるときに sendBeacon で送る最大リクエスト数(残りは localStorage へ退避)。 */
const HIDE_FLUSH_MAX_REQUESTS = 3;
const LABEL_MAX_LENGTH = 30;
// バックエンド models/ui_log.py の max_length と揃える。1件でも超えると 422 でバッチ全体
// (最大100件)が破棄されるため、送る前にここで丸める。
const MAX_USER_ID = 64;
const MAX_SCREEN = 32;
const MAX_TAG = 16;
const MAX_LAYOUT_MODE = 16;

/** 反応する要素とみなすセレクタ。祖先にこれがあれば「操作要素へのタップ」。 */
const INTERACTIVE_SELECTOR =
    'button, a[href], [role="button"], [role="tab"], [role="menuitem"], input, select, textarea, summary, label, [data-track]';

export interface TapEvent {
    event_id: string;
    occurred_at_ms: number;
    user_id: string | null;
    screen: string;
    element_id: string | null;
    element_tag: string | null;
    is_interactive: boolean;
    x_pct: number | null;
    y_pct: number | null;
    layout_mode: string | null;
}

export interface TapBatch {
    session_id: string;
    events: TapEvent[];
}

/** タップ時点の文脈(App が持つ状態)。 */
export interface TapContext {
    userId: string | null;
    screen: string;
    layoutMode: string | null;
}

/** 送信結果。'drop' は再送しても成功しない(4xx等)ため破棄する。 */
export type SendResult = 'ok' | 'retry' | 'drop';

export interface TapLoggerDeps {
    getContext: () => TapContext;
    send: (batch: TapBatch) => Promise<SendResult>;
    /** 画面を隠す/閉じる時の送信。キューに積めたら true。 */
    beacon: (batch: TapBatch) => boolean;
    storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | null;
    now: () => number;
    uuid: () => string;
}

export interface TargetDescription {
    element_id: string | null;
    element_tag: string | null;
    is_interactive: boolean;
}

function truncate(text: string, max: number = LABEL_MAX_LENGTH): string {
    return text.length > max ? text.slice(0, max) : text;
}

/**
 * タップされた要素を記録用の短い識別子にする。
 * 識別子の優先順位: data-track > aria-label > (入力欄は name/placeholder/type のみ。値は使わない)
 * > 文言の先頭30文字。操作要素が祖先に無ければ「反応しない場所へのタップ」として
 * element_id なし・タップされた要素のタグだけを返す。
 */
export function describeTarget(target: EventTarget | null): TargetDescription {
    if (!(target instanceof Element)) {
        return { element_id: null, element_tag: null, is_interactive: false };
    }
    const interactive = target.closest(INTERACTIVE_SELECTOR);
    if (!interactive) {
        return { element_id: null, element_tag: truncate(target.tagName.toLowerCase(), MAX_TAG), is_interactive: false };
    }

    const tag = interactive.tagName.toLowerCase();
    let label = interactive.getAttribute('data-track') || interactive.getAttribute('aria-label') || '';
    if (!label && (tag === 'input' || tag === 'select' || tag === 'textarea')) {
        // 入力欄は value を絶対に読まない(個人的な入力内容を記録しないため)。
        label = interactive.getAttribute('name') || interactive.getAttribute('placeholder')
            || interactive.getAttribute('type') || tag;
    }
    if (!label) {
        label = (interactive.textContent ?? '').replace(/\s+/g, ' ').trim();
    }
    return {
        element_id: label ? truncate(label) : tag,
        element_tag: truncate(tag, MAX_TAG),
        is_interactive: true,
    };
}

function truncateOrNull(text: string | null | undefined, max: number): string | null {
    return text ? truncate(text, max) : null;
}

function toPct(value: number, total: number): number | null {
    if (!Number.isFinite(value) || !Number.isFinite(total) || total <= 0) return null;
    const pct = (value / total) * 100;
    return Math.round(Math.min(100, Math.max(0, pct)) * 10) / 10;
}

export class TapLogger {
    private queue: TapEvent[] = [];
    private sending = false;
    private failures = 0;
    private retryAt = 0;
    private readonly sessionId: string;

    constructor(private readonly deps: TapLoggerDeps) {
        this.sessionId = deps.uuid();
    }

    /** テスト・検査用。 */
    get pending(): readonly TapEvent[] {
        return this.queue;
    }

    /** キューへ追加する。20件たまったら送信を試みる。 */
    enqueue(event: TapEvent): void {
        this.queue.push(event);
        if (this.queue.length > TAP_STORAGE_MAX) {
            this.queue.splice(0, this.queue.length - TAP_STORAGE_MAX);
        }
        if (this.queue.length >= TAP_FLUSH_THRESHOLD) void this.flush();
    }

    /** pointerdown から TapEvent を組み立ててキューへ積む。 */
    record(e: PointerEvent): void {
        if (!e.isPrimary) return;
        if (e.pointerType === 'mouse' && e.button !== 0) return;
        const ctx = this.deps.getContext();
        const target = describeTarget(e.target);
        // 横画面の4人パネルのように、画面の一部が「この人・このタブ」を自己申告している場合は
        // それを優先する(App が持つ選択中ユーザー/タブとは別の人を操作しているため)。
        const scope = e.target instanceof Element ? e.target.closest<HTMLElement>('[data-tap-user]') : null;
        this.enqueue({
            event_id: this.deps.uuid(),
            occurred_at_ms: this.deps.now(),
            user_id: truncateOrNull(scope?.dataset.tapUser || ctx.userId, MAX_USER_ID),
            screen: truncate(scope?.dataset.tapScreen || ctx.screen, MAX_SCREEN),
            ...target,
            x_pct: toPct(e.clientX, window.innerWidth),
            y_pct: toPct(e.clientY, window.innerHeight),
            layout_mode: truncateOrNull(ctx.layoutMode, MAX_LAYOUT_MODE),
        });
    }

    /** 先頭から最大100件を送る。バックオフ中・送信中・空のときは何もしない。 */
    async flush(): Promise<void> {
        if (this.sending || this.queue.length === 0 || this.deps.now() < this.retryAt) return;
        this.sending = true;
        const chunk = this.queue.slice(0, TAP_MAX_BATCH);
        let result: SendResult;
        try {
            result = await this.deps.send({ session_id: this.sessionId, events: chunk });
        } catch {
            result = 'retry';
        }
        this.sending = false;

        if (result === 'retry') {
            this.failures += 1;
            this.retryAt = this.deps.now()
                + Math.min(TAP_BACKOFF_BASE_MS * 2 ** (this.failures - 1), TAP_BACKOFF_MAX_MS);
            this.persist();
            return;
        }
        // 'ok' / 'drop' はどちらも送った分をキューから外す(await中にキューが変わっていても
        // event_id で外すので、新しく積まれたタップは残る)。
        this.removeSent(chunk);
        this.failures = 0;
        this.retryAt = 0;
        this.persist();
        if (this.queue.length >= TAP_FLUSH_THRESHOLD) void this.flush();
    }

    /** 画面を隠す/閉じるとき。fetch は途中で打ち切られうるため sendBeacon で送る。 */
    flushOnHide(): void {
        for (let i = 0; i < HIDE_FLUSH_MAX_REQUESTS && this.queue.length > 0; i++) {
            const chunk = this.queue.slice(0, TAP_MAX_BATCH);
            if (!this.deps.beacon({ session_id: this.sessionId, events: chunk })) break;
            this.removeSent(chunk);
        }
        this.persist();
    }

    /** 前回の未送信分(localStorage)をキューの先頭へ戻す。 */
    restore(): void {
        const { storage } = this.deps;
        if (!storage) return;
        try {
            const raw = storage.getItem(TAP_STORAGE_KEY);
            if (!raw) return;
            const parsed: unknown = JSON.parse(raw);
            if (!Array.isArray(parsed)) return;
            const known = new Set(this.queue.map(ev => ev.event_id));
            const restored = parsed.filter(
                (ev): ev is TapEvent => !!ev && typeof ev === 'object'
                    && typeof (ev as TapEvent).event_id === 'string'
                    && typeof (ev as TapEvent).occurred_at_ms === 'number'
                    && !known.has((ev as TapEvent).event_id),
            );
            this.queue = [...restored, ...this.queue].slice(-TAP_STORAGE_MAX);
        } catch {
            // 壊れた保存データは無視する
        }
    }

    private removeSent(sent: TapEvent[]): void {
        const ids = new Set(sent.map(ev => ev.event_id));
        this.queue = this.queue.filter(ev => !ids.has(ev.event_id));
    }

    private persist(): void {
        const { storage } = this.deps;
        if (!storage) return;
        try {
            if (this.queue.length === 0) storage.removeItem(TAP_STORAGE_KEY);
            else storage.setItem(TAP_STORAGE_KEY, JSON.stringify(this.queue));
        } catch {
            // localStorage が使えない/満杯のときは退避を諦める(ログ欠損より動作を優先する)
        }
    }

    /**
     * リスナーとタイマーを登録し、解除関数を返す。
     * pointerdown は passive + capture で、アプリ側のイベント処理を一切妨げない。
     */
    start(): () => void {
        this.restore();
        const onPointerDown = (e: PointerEvent) => this.record(e);
        const onHide = () => {
            if (document.visibilityState === 'hidden') this.flushOnHide();
        };
        const onPageHide = () => this.flushOnHide();
        const onOnline = () => { this.retryAt = 0; void this.flush(); };

        document.addEventListener('pointerdown', onPointerDown, { capture: true, passive: true });
        document.addEventListener('visibilitychange', onHide);
        window.addEventListener('pagehide', onPageHide);
        window.addEventListener('online', onOnline);
        const timer = window.setInterval(() => { void this.flush(); }, TAP_FLUSH_INTERVAL_MS);
        // 前回の未送信分があれば、起動直後に送り始める。
        void this.flush();

        return () => {
            document.removeEventListener('pointerdown', onPointerDown, { capture: true });
            document.removeEventListener('visibilitychange', onHide);
            window.removeEventListener('pagehide', onPageHide);
            window.removeEventListener('online', onOnline);
            window.clearInterval(timer);
            this.persist();
        };
    }
}

function defaultUuid(): string {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return crypto.randomUUID();
    }
    // randomUUID が無い古い環境(非セキュアコンテキスト等)向け。一意であれば十分(暗号用途ではない)。
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

function defaultStorage(): TapLoggerDeps['storage'] {
    try {
        return typeof window === 'undefined' ? null : window.localStorage;
    } catch {
        return null;
    }
}

/** 本番用の依存(fetch + sendBeacon + localStorage)で TapLogger を作る。 */
export function createTapLogger(getContext: () => TapContext): TapLogger {
    const url = apiClient.url(TAP_LOG_ENDPOINT);
    return new TapLogger({
        getContext,
        send: async (batch) => {
            try {
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(batch),
                    keepalive: true,
                });
                if (res.ok) return 'ok';
                // 408/429 と 5xx は再送で成功しうる。それ以外の4xx(422等)は再送しても同じなので破棄する。
                return res.status === 408 || res.status === 429 || res.status >= 500 ? 'retry' : 'drop';
            } catch {
                return 'retry';
            }
        },
        beacon: (batch) => {
            if (typeof navigator === 'undefined' || typeof navigator.sendBeacon !== 'function') return false;
            try {
                return navigator.sendBeacon(url, new Blob([JSON.stringify(batch)], { type: 'application/json' }));
            } catch {
                return false;
            }
        },
        storage: defaultStorage(),
        now: () => Date.now(),
        uuid: defaultUuid,
    });
}
