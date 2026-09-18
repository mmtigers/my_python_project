// family-quest/src/lib/pathSegments.ts

// routing.ts(isCameraRoute)とoutOfScopeReload.ts(isOutsideServiceWorkerScope)が
// それぞれ独自にpathname.split('/').filter(Boolean)を行っていた重複を解消する
// ための共有ヘルパー(コードレビュー指摘対応)。パスの先頭スラッシュ・末尾スラッシュ
// による空文字列の混入を取り除いたセグメント配列を返す。
export function getPathSegments(pathname: string): string[] {
    return pathname.split('/').filter(Boolean);
}
