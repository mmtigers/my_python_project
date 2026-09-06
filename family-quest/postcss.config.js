export default {
    plugins: {
        // Tailwind v4でPostCSSプラグインは本体から @tailwindcss/postcss へ分離された。
        // ベンダープレフィックスの付与はv4本体が内包するため autoprefixer は不要。
        "@tailwindcss/postcss": {},
    },
}
