import { useCallback, useState } from 'react';
import {
    LATEST_CHANGELOG_ENTRY,
    readSeenChangelogVersion,
    writeSeenChangelogVersion,
} from '../lib/changelogSeen';

// 最新のアップデートをこの端末でまだ見ていないか。見た(モーダルを開いた/帯を閉じた)ら
// markSeen() で既読にし、次に CHANGELOG へ新しい版が追記されるまでバッジ・帯を出さない。
export function useChangelogUnread() {
    const latest = LATEST_CHANGELOG_ENTRY;
    const [seenVersion, setSeenVersion] = useState<string | null>(() => readSeenChangelogVersion());

    const markSeen = useCallback(() => {
        if (!latest) return;
        writeSeenChangelogVersion(latest.version);
        setSeenVersion(latest.version);
    }, [latest]);

    const hasUnread = !!latest && seenVersion !== latest.version;
    return { hasUnread, latest, markSeen };
}
