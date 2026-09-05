// family-quest/src/lib/routing.ts

// #472: main.tsxのルートビュー切り替え判定を、単体テスト可能な純粋関数として分離する。
//
// バックエンド(MY_HOME_SYSTEM/unified_server.py)は '/camera' 配下を専用ルートで、
// '/quest' 配下をSPAのcatch-allフォールバック(index.htmlへ委譲)で配信している。
// そのため実際にカメラビューとして扱うべきパスは '/camera'・'/camera/...' に加えて、
// '/quest' 配下のSPAが自身でクライアントサイド判定する '/quest/camera'・
// '/quest/camera/...' も含む(main.tsxの元コメント参照)。
//
// 以前は pathname.includes('/camera') という単純な部分一致で判定しており、
// 将来 '/settings/camera-help' のような無関係なパスを追加すると、意図せず
// CameraDashboardがマウントされてしまう恐れがあった。パスをセグメント単位に
// 分割し、先頭セグメントが 'camera' であるか、先頭が 'quest' で2番目が
// 'camera' である場合のみカメラビューとして扱う。
export function isCameraRoute(pathname: string): boolean {
    const segments = pathname.split('/').filter(Boolean);
    return segments[0] === 'camera' || (segments[0] === 'quest' && segments[1] === 'camera');
}
