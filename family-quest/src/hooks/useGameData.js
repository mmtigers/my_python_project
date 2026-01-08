import { useState, useEffect, useCallback } from 'react';
import { apiClient } from '../utils/apiClient';
import { INITIAL_USERS, MASTER_QUESTS, MASTER_REWARDS } from '../constants/masterData';

export const useGameData = (onLevelUp) => {
    const [users, setUsers] = useState(INITIAL_USERS || []);
    const [quests, setQuests] = useState(MASTER_QUESTS || []);
    const [rewards, setRewards] = useState(MASTER_REWARDS || []);
    const [completedQuests, setCompletedQuests] = useState([]);
    const [pendingQuests, setPendingQuests] = useState([]);
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
            if (data.pendingQuests) setPendingQuests(data.pendingQuests);
            if (data.logs) setAdventureLogs(data.logs);
            if (data.equipments) setEquipments(data.equipments);
            if (data.ownedEquipments) setOwnedEquipments(data.ownedEquipments);

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

    // ★追加: キャンセル専用の関数 (App.jsxのモーダルから呼ばれる)
    const cancelQuest = async (currentUser, historyItem) => {
        try {
            await apiClient.post('/api/quest/quest/cancel', {
                user_id: currentUser.user_id,
                history_id: historyItem.id || historyItem.history_id
            });
            await fetchGameData();
        } catch (e) {
            alert(`キャンセル失敗: ${e.message}`);
        }
    };

    const completeQuest = async (currentUser, quest) => {
        const q_id = quest.quest_id || quest.id;

        const isPending = pendingQuests.some(pq => pq.user_id === currentUser.user_id && pq.quest_id === q_id);
        if (isPending) {
            alert("親の承認待ちです。承認されるまでお待ちください！");
            return;
        }

        // ▼ 修正: ここにあったキャンセル分岐(completedEntryの確認)は削除しました。
        // App.jsx 側で「完了済みならキャンセルモーダルを出す」制御をしているため、
        // この関数が呼ばれるときは「新規完了アクション」のみとなります。

        try {
            const res = await apiClient.post('/api/quest/complete', {
                user_id: currentUser.user_id,
                quest_id: q_id
            });
            await fetchGameData();

            if (res.message) {
                alert(res.message);
            }

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
    };

    const approveQuest = async (currentUser, historyItem) => {
        if (!['dad', 'mom'].includes(currentUser.user_id)) {
            alert("承認権限がありません！");
            return;
        }

        if (!window.confirm(`${historyItem.quest_title} を承認しますか？`)) return;

        try {
            const res = await apiClient.post('/api/quest/approve', {
                approver_id: currentUser.user_id,
                history_id: historyItem.id
            });
            await fetchGameData();

            if (res.leveledUp && onLevelUp) {
                // 必要ならレベルアップ通知
            }
        } catch (e) {
            alert(`承認失敗: ${e.message}`);
        }
    };

    const rejectQuest = async (currentUser, historyItem) => {
        if (!['dad', 'mom'].includes(currentUser.user_id)) {
            alert("権限がありません！");
            return;
        }

        if (!window.confirm(`${historyItem.quest_title} を再チャレンジ（却下）にしますか？\n子供側の完了状態が解除されます。`)) return;

        try {
            await apiClient.post('/api/quest/reject', {
                approver_id: currentUser.user_id,
                history_id: historyItem.id
            });
            await fetchGameData();
        } catch (e) {
            alert(`却下失敗: ${e.message}`);
        }
    };

    const buyReward = async (currentUser, reward) => {
        const cost = reward.cost_gold || reward.cost;
        if ((currentUser?.gold || 0) < cost) {
            alert("ゴールドが足りません！");
            return;
        }


        try {
            const res = await apiClient.post('/api/quest/reward/purchase', {
                user_id: currentUser.user_id,
                reward_id: reward.reward_id || reward.id
            });
            await fetchGameData();
            return { success: true, newGold: res.newGold, reward: reward };

        } catch (e) {
            alert(`購入失敗: ${e.message}`);
            return { success: false, error: e.message };
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
        users, quests, rewards, completedQuests, pendingQuests, adventureLogs, isLoading,
        equipments, ownedEquipments,
        familyStats, chronicle,
        completeQuest, approveQuest, rejectQuest, cancelQuest, // ★ ここに cancelQuest を追加してエクスポート
        buyReward, buyEquipment, changeEquipment,
        fetchGameData
    };
};