import React from 'react';
import { useTvBlock } from '../../hooks/useTvBlock';
import TvBlockBanner from '../ui/TvBlockBanner';

// 画面上部(ヘッダー直下)の、全タブ・全ユーザー共通のテレビおやすみ帯。
// ごほうび画面を開かなくても、家族の誰でも気づけるようにする。平日・取得失敗時は何も出さない。
const TvBlockHeaderBanner: React.FC = () => {
    const { tvBlock, tvSecondsLeft, tvBlockActive } = useTvBlock();
    if (!tvBlock) return null;
    return (
        <TvBlockBanner tvBlock={tvBlock} tvSecondsLeft={tvSecondsLeft} tvBlockActive={tvBlockActive} large />
    );
};

export default TvBlockHeaderBanner;
