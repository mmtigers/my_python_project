import React from 'react';

// カードのバリエーション定義
type CardVariant = 'default' | 'completed' | 'pending' | 'infinite' | 'timeLimit' | 'random' | 'limited';

interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
    variant?: CardVariant;
    children: React.ReactNode;
}

export const Card: React.FC<CardProps> = ({ variant = 'default', className = '', children, ...props }) => {
    // バリエーションごとのスタイル定義
    const baseStyle = "border p-2 rounded flex justify-between items-center transition-all relative overflow-hidden";

    let variantStyle = "border-white bg-blue-900/80 hover:bg-blue-800 hover:border-yellow-200"; // default

    switch (variant) {
        case 'completed':
            variantStyle = "border-gray-600 bg-gray-900/50 grayscale";
            break;
        case 'pending':
            variantStyle = "border-yellow-500 bg-yellow-900/40";
            break;
        case 'infinite':
            variantStyle = "border-cyan-400 bg-cyan-950/90 hover:bg-cyan-900 shadow-[0_0_8px_rgba(0,255,255,0.2)]";
            break;
        case 'timeLimit':
            variantStyle = "border-orange-400 bg-gradient-to-r from-orange-900/90 to-red-900/90 hover:from-orange-800 hover:to-red-800 shadow-[0_0_10px_rgba(255,165,0,0.3)]";
            break;
        case 'random':
            variantStyle = "border-purple-400 bg-purple-950/90 hover:bg-purple-900";
            break;
        case 'limited':
            variantStyle = "border-pink-400 bg-pink-950/90 hover:bg-pink-900";
            break;
    }

    // 押せる感じ（カーソルポインター）にするかどうか
    const interactiveStyle = props.onClick ? "cursor-pointer active:scale-[0.98] select-none" : "";

    return (
        <div className={`${baseStyle} ${variantStyle} ${interactiveStyle} ${className}`} {...props}>
            {children}
        </div>
    );
};