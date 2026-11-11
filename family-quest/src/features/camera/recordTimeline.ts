// Issue #1091: 録画VODのプレイリストは NAS 上の録画 MP4 をそのまま並べたもので、
// 録画が欠けている時間帯は詰まっている(以前の ffmpeg 生成版は欠けた時間を最終フレームの
// 静止で埋めていたので「再生位置 = 最初のファイルからの経過秒」で済んだ)。
// そのため、指定時刻からプレイリスト上の再生位置への変換には、バックエンドの
// `/api/cameras/record/{camera_id}/{date}/info` が返すファイルごとの対応表を使う。

/** 録画ファイル1本分の「時刻 ↔ プレイリスト上の位置」の対応。 */
export interface RecordTimelineEntry {
    /** 録画開始時刻(0時からの秒数) */
    start_seconds: number;
    /** プレイリスト上の開始位置(秒) */
    position: number;
    /** 長さ(秒) */
    duration: number;
}

/** `/info` のレスポンス。files は Issue #1091 で追加(古いバックエンドでは無い)。 */
export interface RecordInfo {
    offset_seconds: number;
    files?: RecordTimelineEntry[];
}

/** 指定時刻が最後の録画より後だったときに、終わりの何秒前から再生するか */
const TAIL_SECONDS = 5;

/**
 * 指定時刻(0時からの秒数)を、その日の録画プレイリスト上の再生位置(秒)に変換する。
 *
 * - 録画中の時刻: そのファイルの位置 + ファイル内の経過秒
 * - 録画が欠けている時刻(最初の録画より前を含む): 次の録画の先頭
 * - 最後の録画より後: 最後の録画の終わり近く
 * - 対応表が無い(古いバックエンド・録画なし): 最初のファイルの開始時刻からの経過秒
 */
export function recordPositionForTime(info: RecordInfo, targetSeconds: number): number {
    const files = info.files ?? [];
    if (files.length === 0) {
        return Math.max(0, targetSeconds - info.offset_seconds);
    }
    for (const file of files) {
        if (targetSeconds < file.start_seconds) {
            return file.position;
        }
        if (targetSeconds < file.start_seconds + file.duration) {
            return file.position + (targetSeconds - file.start_seconds);
        }
    }
    const last = files[files.length - 1];
    return Math.max(last.position, last.position + last.duration - TAIL_SECONDS);
}
