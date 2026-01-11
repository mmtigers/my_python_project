import React from 'react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { Zap } from 'lucide-react';
import { motion } from 'framer-motion';

// レベルアップ情報の型定義
interface LevelUpInfo {
    user: string;
    level: number;
    job: string;
}

interface LevelUpModalProps {
    info: LevelUpInfo | null;
    onClose: () => void;
}

const LevelUpModal: React.FC<LevelUpModalProps> = ({ info, onClose }) => {
    if (!info) return null;

    return (
        <Modal isOpen={true} onClose={onClose} maxWidth="sm">
            <motion.div
                className="text-center py-4"
                // ポップアップアニメーション（バネの動き）
                initial={{ scale: 0.5, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                transition={{ type: "spring", stiffness: 300, damping: 20 }}
            >
                <motion.div
                    className="relative inline-block"
                    // アイコンの回転出現アニメーション
                    initial={{ rotate: -180, scale: 0 }}
                    animate={{ rotate: 0, scale: 1 }}
                    transition={{ delay: 0.2, type: "spring", stiffness: 200 }}
                >
                    <Zap size={64} className="text-yellow-400 animate-bounce mx-auto mb-4" />
                    <div className="absolute inset-0 animate-ping opacity-75">
                        <Zap size={64} className="text-yellow-200" />
                    </div>
                </motion.div>

                <h2 className="text-2xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-yellow-300 to-orange-500 mb-2">
                    LEVEL UP!
                </h2>

                <div className="bg-slate-900/50 p-4 rounded-lg border border-slate-700 mb-6">
                    <div className="text-gray-300 text-sm mb-1">{info.user} は</div>
                    <motion.div
                        className="text-3xl font-black text-white mb-1"
                        initial={{ scale: 1 }}
                        animate={{ scale: [1, 1.2, 1] }} // 少し鼓動させる
                        transition={{ delay: 0.5, duration: 0.4 }}
                    >
                        Lv.{info.level}
                    </motion.div>
                    <div className="text-gray-400 text-xs">になった！</div>

                    <div className="mt-4 pt-4 border-t border-slate-700 text-xs text-yellow-500 font-bold">
                        ステータスが大幅アップ！<br />
                        新しいクエストに挑戦しよう！
                    </div>
                </div>

                <Button onClick={onClose} variant="primary" size="lg" className="w-full animate-pulse">
                    最高だぜ！
                </Button>
            </motion.div>
        </Modal>
    );
};

export default LevelUpModal;