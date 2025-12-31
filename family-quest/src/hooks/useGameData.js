// family-quest/src/hooks/useGameData.js
import { useState, useEffect, useCallback } from 'react';
import { apiClient } from '../utils/apiClient';
import { INITIAL_USERS, MASTER_QUESTS, MASTER_REWARDS } from '../constants/masterData';

export const useGameData = (onLevelUp) => {
    const [users, setUsers] = useState(INITIAL_USERS || []);
    const [quests, setQuests] = useState(MASTER_QUESTS || []);
    const [rewards, setRewards] = useState(MASTER_REWARDS || []);
    const [completedQuests, setCompletedQuests] = useState([]);
    const [adventureLogs, setAdventureLogs] = useState([]);
    const [isLoading, setIsLoading] = useState(true);

    const [equipments, setEquipments] = useState([]);
    const [ownedEquipments, setOwnedEquipments] = useState([]);
    const [familyStats, setFamilyStats] = useState(null);
    const [chronicle, setChronicle] = useState([]);

    const fetchGameData = useCallback(async () => {
        try {
            const data = await apiClient.get('/api/quest/data');

            if (data.users) setUsers(data.users);
            if (data.quests) setQuests(data.quests);
            if (data.rewards) setRewards(data.rewards);
            if (data.completedQuests) setCompletedQuests(data.completedQuests);
            if (data.logs) setAdventureLogs(data.logs);
            if (data.equipments) setEquipments(data.equipments);
            if (data.ownedEquipments) setOwnedEquipments(data.ownedEquipments);

            if (data.boss) {
                // ボスデータがあればここでセット（今回はParty側で処理しているが、拡張性を考慮）
            }

            // 記録データの取得
            try {
                const chronicleData = await apiClient.get('/api/quest/family/chronicle');
                if (chronicleData) {
                    setFamilyStats(chronicleData.stats);
                    setChronicle(chronicleData.chronicle);
                }
            } catch (err) {
                console.warn("Chronicle data fetch failed:", err);
            }

            setIsLoading(false);
        } catch (error) {
            console.error("Game Data Load Error:", error);
            setIsLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchGameData();
    }, [fetchGameData]);

    const completeQuest = async (currentUser, quest) => {
        const q_id = quest.quest_id || quest.id;
        const completedEntry = completedQuests.find(
            q => q.user_id === currentUser.user_id && q.quest_id === q_id
        );

        if (completedEntry) {
            if (!window.confirm("この行動を 取り消しますか？")) return;
            try {
                await apiClient.post('/api/quest/quest/cancel', {
                    user_id: currentUser.user_id,
                    history_id: completedEntry.id
                });
                await fetchGameData();
            } catch (e) {
                alert(`キャンセル失敗: ${e.message}`);
            }
        } else {
            try {
                const res = await apiClient.post('/api/quest/complete', {
                    user_id: currentUser.user_id,
                    quest_id: q_id
                });
                await fetchGameData();

                if (res.earnedMedals > 0) {
                    alert(`✨ ラッキー！！ ✨\nちいさなメダル を見つけた！`);
                }

                if (res.leveledUp && onLevelUp) {
                    onLevelUp({
                        user: currentUser.name,
                        level: res.newLevel,
                        job: currentUser.job_class
                    });
                }
            } catch (e) {
                alert(`クエスト完了失敗: ${e.message}`);
            }
        }
    };

    const buyReward = async (currentUser, reward) => {
        const cost = reward.cost_gold || reward.cost;
        if ((currentUser?.gold || 0) < cost) {
            alert("ゴールドが足りません！");
            return;
        }
        if (!window.confirm(`${reward.title} を 購入しますか？`)) return;

        try {
            const res = await apiClient.post('/api/quest/reward/purchase', {
                user_id: currentUser.user_id,
                reward_id: reward.reward_id || reward.id
            });
            await fetchGameData();
            alert(`まいどあり！\n${reward.title} を手に入れた！\n(残金: ${res.newGold} G)`);
        } catch (e) {
            alert(`購入失敗: ${e.message}`);
        }
    };

    const buyEquipment = async (currentUser, item) => {
        if ((currentUser?.gold || 0) < item.cost) {
            alert("ゴールドが足りません！");
            return;
        }
        if (!window.confirm(`${item.name} を購入しますか？`)) return;

        try {
            await apiClient.post('/api/quest/equip/purchase', {
                user_id: currentUser.user_id,
                equipment_id: item.equipment_id
            });
            await fetchGameData();
            alert(`チャキーン！\n${item.name} を手に入れた！`);
        } catch (e) {
            alert(`購入失敗: ${e.message}`);
        }
    };

    const changeEquipment = async (currentUser, item) => {
        try {
            await apiClient.post('/api/quest/equip/change', {
                user_id: currentUser.user_id,
                equipment_id: item.equipment_id
            });
            await fetchGameData();
        } catch (e) {
            alert(`装備変更失敗: ${e.message}`);
        }
    };

    return {
        users, quests, rewards, completedQuests, adventureLogs, isLoading,
        equipments, ownedEquipments,
        familyStats, chronicle,
        completeQuest, buyReward, buyEquipment, changeEquipment,
        fetchGameData
    };
};