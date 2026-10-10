// アプリ内「アップデートのれきし」に表示する更新履歴。
// データの正は changelog.json(新しい順、先頭が最新。手動で追記する)。JSON にしているのは、
// お家ダッシュボード(MY_HOME_SYSTEM)の「アップデート」ページが同じファイルを直接読んで
// 全アプリの更新履歴を一覧表示するため(TypeScript を解析せずに済み、二重管理にもならない)。
// ここで管理する version は履歴専用の版数で、package.json の version とは連動しない。
import changelogData from './changelog.json';

export interface ChangelogEntry {
    version: string;
    // 'YYYY-MM-DD'
    date: string;
    changes: string[];
}

export const CHANGELOG: readonly ChangelogEntry[] = changelogData;
