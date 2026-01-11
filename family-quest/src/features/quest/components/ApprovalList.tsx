import React from 'react';
import { CheckCircle, XCircle } from 'lucide-react';
import { User, QuestHistory } from '@/types';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';

interface ApprovalListProps {
    pendingQuests: QuestHistory[];
    users: User[];
    onApprove: (quest: QuestHistory) => void;
    onReject: (quest: QuestHistory) => void;
}

const ApprovalList: React.FC<ApprovalListProps> = ({ pendingQuests, users, onApprove, onReject }) => {
    return (
        // Cardの variant="default" をベースに、背景色などを上書きします
        <Card className="p-4 bg-orange-900/30 border-orange-500 animate-pulse-slow block">
            <div className="font-bold text-orange-300 mb-2 flex items-center gap-2">
                <span className="animate-bounce">⚡</span>
                承認待ちのクエスト
            </div>
            <div className="space-y-2">
                {pendingQuests.map((quest) => {
                    const quester = users.find(u => u.user_id === quest.user_id);
                    // 各リストアイテムはCardではなく単純なdivで表現
                    return (
                        <div key={quest.id || quest.history_id} className="bg-black/40 p-2 rounded flex justify-between items-center border border-white/10">
                            <div>
                                <div className="text-sm font-bold text-white">{quest.quest_title}</div>
                                <div className="text-xs text-gray-400">by {quester?.name}</div>
                            </div>
                            <div className="flex gap-2">
                                <Button size="sm" variant="danger" onClick={() => onReject(quest)}>
                                    <XCircle size={16} />
                                </Button>
                                <Button size="sm" variant="success" onClick={() => onApprove(quest)}>
                                    <CheckCircle size={16} />
                                </Button>
                            </div>
                        </div>
                    );
                })}
            </div>
        </Card>
    );
};

export default ApprovalList;