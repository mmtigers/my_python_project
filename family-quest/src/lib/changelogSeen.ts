import { CHANGELOG } from './changelog';

// 「どのアップデートまで見たか」を端末ごとに覚えておくキー。家族の端末ごとに
// 別々に「新しいアップデート」を知らせるため、サーバーではなく localStorage に持つ。
const SEEN_VERSION_KEY = 'familyQuest.changelog.seenVersion';

// 最新のアップデート(CHANGELOGは新しい順で、先頭が最新)。
export const LATEST_CHANGELOG_ENTRY = CHANGELOG[0];

// localStorage は private window・サイトデータのブロック等で例外になりうるため、
// 読み書きは必ず try/catch で囲む(使えないときは毎回「未読」扱いになるだけで画面は壊さない)。
export function readSeenChangelogVersion(): string | null {
    try {
        return window.localStorage.getItem(SEEN_VERSION_KEY);
    } catch {
        return null;
    }
}

export function writeSeenChangelogVersion(version: string): void {
    try {
        window.localStorage.setItem(SEEN_VERSION_KEY, version);
    } catch {
        // 保存できなくても致命的ではない
    }
}
