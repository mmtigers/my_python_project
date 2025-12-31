// family-quest/src/components/quest/ApprovalList.jsx
import React from 'react';
import { CheckCircle2, XCircle } from 'lucide-react';

const ApprovalList = ({ pendingQuests, users, onApprove }) => {
    if (!pendingQuests || pendingQuests.length === 0) return null;

    // ユーザーIDから名前を引けるようにするマップ
    const userMap = users.reduce((acc, u) => {
        acc[u.user_id] = u;
        return acc;
    }, {});

    return (
        <div className="bg-yellow-900/30 border-2 border-yellow-500/50 rounded-lg p-3 mb-4 animate-in slide-in-from-top-2">
            <h3 className="text-yellow-300 font-bold text-sm mb-2 flex items-center gap-2">
                <CheckCircle2 size={16} /> 承認待ちクエスト ({pendingQuests.length})
            </h3>
            <div className="space-y-2">
                {pendingQuests.map((quest) => {
                    const user = userMap[quest.user_id] || { name: 'Unknown', avatar: '?' };
                    return (
                        <div key={quest.id} className="bg-black/40 border border-yellow-500/30 rounded p-2 flex justify-between items-center">
                            <div className="flex items-center gap-2">
                                <span className="text-xl bg-gray-800 rounded p-1">{user.avatar}</span>
                                <div>
                                    <div className="text-xs text-gray-300">{user.name}</div>
                                    <div className="font-bold text-sm text-white">{quest.quest_title}</div>
                                </div>
                            </div>
                            <button
                                onClick={() => onApprove(quest)}
                                className="bg-yellow-600 hover:bg-yellow-500 text-white text-xs font-bold py-1.5 px-3 rounded shadow active:scale-95 transition-all flex items-center gap-1"
                            >
                                <CheckCircle2 size={14} /> 承認
                            </button>
                        </div>
                    );
                })}
            </div>
        </div>
    );
};

export default ApprovalList;