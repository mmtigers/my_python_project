import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import LiveView from './LiveView';
import { CameraConfig } from '../types';

// HlsPlayer は HLS.js を立ち上げるため、jsdom では差し替える。
// streamUrl はこのコンポーネントが組み立てる唯一の出力なので、
// data 属性に出して検証できるようにしておく。
vi.mock('../../../components/ui/HlsPlayer', () => ({
    default: (props: { streamUrl: string; controls?: boolean }) => (
        <div data-testid="player" data-stream={props.streamUrl} data-controls={String(!!props.controls)} />
    ),
}));

const cameras: CameraConfig[] = [
    { id: 'entrance', name: '玄関', order: 1, enabled: true },
    { id: 'garden', name: '庭', order: 2, enabled: true },
];

afterEach(cleanup);

describe('LiveView', () => {
    it('4分割表示では全カメラ分のプレイヤーを並べる', () => {
        render(<LiveView cameras={cameras} />);
        const players = screen.getAllByTestId('player');
        expect(players).toHaveLength(2);
        expect(players.map(p => p.dataset.stream)).toEqual([
            '/api/cameras/live/entrance/stream.m3u8',
            '/api/cameras/live/garden/stream.m3u8',
        ]);
        // 一覧側は操作用の小窓なのでコントロールを出さない
        expect(players.every(p => p.dataset.controls === 'false')).toBe(true);
        expect(screen.queryByText('◀ 4分割に戻る')).not.toBeInTheDocument();
    });

    it('カメラをクリックすると、そのカメラだけの拡大表示に切り替わる', () => {
        render(<LiveView cameras={cameras} />);
        fireEvent.click(screen.getByLabelText('庭を拡大表示'));

        const players = screen.getAllByTestId('player');
        expect(players).toHaveLength(1);
        expect(players[0].dataset.stream).toBe('/api/cameras/live/garden/stream.m3u8');
        // 拡大表示は視聴用なのでコントロールを出す
        expect(players[0].dataset.controls).toBe('true');
    });

    it('拡大表示から4分割へ戻れる', () => {
        render(<LiveView cameras={cameras} />);
        fireEvent.click(screen.getByLabelText('玄関を拡大表示'));
        fireEvent.click(screen.getByText('◀ 4分割に戻る'));
        expect(screen.getAllByTestId('player')).toHaveLength(2);
    });

    // #412(F-L5): HlsPlayer がエラー時に <button> を描画しうるため、タイルは
    // <button> ではなく role="button" の <div> で実装されている。その代償として
    // キーボード操作は手書きの onKeyDown に依存しており、消えても見た目には
    // 気づけないのでここで固定する。
    it.each(['Enter', ' '])('タイルは %s キーでも拡大表示に切り替わる', key => {
        render(<LiveView cameras={cameras} />);
        fireEvent.keyDown(screen.getByLabelText('庭を拡大表示'), { key });
        const players = screen.getAllByTestId('player');
        expect(players).toHaveLength(1);
        expect(players[0].dataset.stream).toBe('/api/cameras/live/garden/stream.m3u8');
    });

    it('関係ないキーでは切り替わらない', () => {
        render(<LiveView cameras={cameras} />);
        fireEvent.keyDown(screen.getByLabelText('庭を拡大表示'), { key: 'a' });
        expect(screen.getAllByTestId('player')).toHaveLength(2);
    });

    it('タイルはキーボードフォーカスを受け取れる', () => {
        render(<LiveView cameras={cameras} />);
        expect(screen.getByLabelText('玄関を拡大表示')).toHaveAttribute('tabIndex', '0');
    });
});
