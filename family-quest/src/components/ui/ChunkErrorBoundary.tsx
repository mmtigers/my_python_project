import React from 'react';

// #362: lazy() で分割したチャンク(AvatarUploader/SettingsModal/CameraDashboard)の
// 読み込みが失敗したときの受け皿。
//
// PWAのService Worker更新(skipWaiting + cleanupOutdatedCaches)で旧ハッシュ付き
// チャンクがprecacheから消えると、常時表示中の旧ページが設定ボタン等を押した瞬間に
// dynamic import が 404 → lazy が throw → React 18 はルートごとアンマウントして
// 白画面になる。ErrorBoundaryが無いとこの白画面から手動リロード以外で復帰できない。
//
// チャンク読込失敗と判定できるエラーは自動でページを再読み込みし(新しいバンドル一式を
// 取り直す)、それ以外の描画エラーは「再読み込み」ボタン付きのフォールバックを表示する。

interface ChunkErrorBoundaryProps {
    children: React.ReactNode;
    // テストから差し替えられるよう、再読み込みの実体は差し替え可能にしておく
    // (jsdom では window.location.reload をモックできないため)。
    reload?: () => void;
}

interface ChunkErrorBoundaryState {
    error: Error | null;
}

// ブラウザ/バンドラごとに文言が異なる「動的importの失敗」を判定するパターン群。
// Chrome/Edge: "Failed to fetch dynamically imported module: ..."
// Safari:      "Importing a module script failed."
// Firefox:     "error loading dynamically imported module"
// webpack系:   "Loading chunk N failed" / "ChunkLoadError"
const CHUNK_LOAD_ERROR_PATTERNS: RegExp[] = [
    /Failed to fetch dynamically imported module/i,
    /Importing a module script failed/i,
    /error loading dynamically imported module/i,
    /Loading (CSS )?chunk [\w-]+ failed/i,
    /ChunkLoadError/i,
];

// 自動リロードが無限ループしないようにするためのガード。直近の自動リロードから
// この時間内に再びチャンク読込失敗が起きた場合は自動リロードせず、フォールバックを出す。
const AUTO_RELOAD_GUARD_MS = 30 * 1000;
const AUTO_RELOAD_GUARD_KEY = 'familyQuest.chunkErrorReloadedAt';

const isChunkLoadError = (error: Error): boolean => {
    const text = `${error.name} ${error.message}`;
    return CHUNK_LOAD_ERROR_PATTERNS.some(re => re.test(text));
};

const readLastAutoReloadAt = (): number => {
    try {
        const raw = window.sessionStorage.getItem(AUTO_RELOAD_GUARD_KEY);
        const value = raw ? Number(raw) : 0;
        return Number.isFinite(value) ? value : 0;
    } catch {
        return 0;
    }
};

const writeLastAutoReloadAt = (at: number): void => {
    try {
        window.sessionStorage.setItem(AUTO_RELOAD_GUARD_KEY, String(at));
    } catch {
        // sessionStorage が使えない環境ではガード無しで進む(リロード自体は行う)
    }
};

const defaultReload = () => window.location.reload();

class ChunkErrorBoundary extends React.Component<ChunkErrorBoundaryProps, ChunkErrorBoundaryState> {
    state: ChunkErrorBoundaryState = { error: null };

    static getDerivedStateFromError(error: Error): ChunkErrorBoundaryState {
        return { error };
    }

    componentDidCatch(error: Error): void {
        console.error('ChunkErrorBoundary caught:', error);
        if (!isChunkLoadError(error)) return;

        const now = Date.now();
        if (now - readLastAutoReloadAt() < AUTO_RELOAD_GUARD_MS) {
            // 直前に自動リロードしたばかりなのにまた失敗した(サーバー側の障害等)。
            // ループさせず、手動の「再読み込み」に委ねる。
            return;
        }
        writeLastAutoReloadAt(now);
        this.handleReload();
    }

    handleReload = (): void => {
        const reload = this.props.reload ?? defaultReload;
        reload();
    };

    render(): React.ReactNode {
        const { error } = this.state;
        if (!error) return this.props.children;

        const chunkError = isChunkLoadError(error);
        return (
            <div
                role="alert"
                className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900 text-gray-100 p-6"
            >
                <div className="max-w-sm w-full bg-slate-800 border-2 border-slate-600 rounded-lg shadow-2xl p-6 text-center space-y-4">
                    <div className="text-4xl">⚠️</div>
                    <p className="font-bold">
                        {chunkError ? '画面の更新が必要です' : '画面の表示に失敗しました'}
                    </p>
                    <p className="text-xs text-slate-400 break-words">
                        {chunkError
                            ? 'アプリが新しいバージョンに更新されました。再読み込みしてください。'
                            : error.message}
                    </p>
                    <button
                        type="button"
                        onClick={this.handleReload}
                        className="w-full min-h-[44px] rounded bg-blue-600 border-2 border-blue-400 font-bold text-white hover:bg-blue-500 transition-colors"
                    >
                        再読み込み
                    </button>
                </div>
            </div>
        );
    }
}

export default ChunkErrorBoundary;
