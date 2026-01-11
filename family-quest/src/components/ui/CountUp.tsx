import { useEffect, useRef } from "react";
import { useSpring, useMotionValue, useTransform, motion } from "framer-motion";

interface CountUpProps {
    value: number;
    className?: string;
    prefix?: string; // "G" や "+" などをつける場合
    suffix?: string;
}

export const CountUp: React.FC<CountUpProps> = ({ value, className, prefix = "", suffix = "" }) => {
    // アニメーションの初期値
    const motionValue = useMotionValue(0);

    // バネの設定 (stiffness: 硬さ, damping: 減衰)
    const springValue = useSpring(motionValue, {
        stiffness: 50,
        damping: 15,
        mass: 1, // 重さ
    });

    // 数値が変わったらアニメーション開始
    useEffect(() => {
        motionValue.set(value);
    }, [value, motionValue]);

    // 数値を表示用に変換 (小数点以下を切り捨てて文字列化)
    const displayValue = useTransform(springValue, (current) => {
        return `${prefix}${Math.round(current).toLocaleString()}${suffix}`;
    });

    return <motion.span className={className}>{displayValue}</motion.span>;
};