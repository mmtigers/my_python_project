export const DAYS = ['日', '月', '火', '水', '木', '金', '土'];

export const getDayIndex = () => new Date().getDay();

export const getCurrentTime = () => {
    const now = new Date();
    return now.getHours() * 100 + now.getMinutes();
};

export const getNextLevelExp = (level) => {
    return Math.floor(100 * Math.pow(1.2, level - 1));
};