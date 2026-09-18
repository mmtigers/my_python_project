// #660: hls.js の light ビルド(`hls.js/light`)は、このバージョンの package.json の
// exports に types エントリを持たないため、TypeScript が型を解決できない。
// 実行時の API はフル版と同じ(字幕・代替音声・EME が省かれるだけ)なので、
// フル版の型宣言をそのまま再エクスポートする。
declare module 'hls.js/light' {
  import Hls from 'hls.js';
  export * from 'hls.js';
  export default Hls;
}
