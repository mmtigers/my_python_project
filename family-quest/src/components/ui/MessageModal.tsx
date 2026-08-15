import React from 'react';
import { Modal } from './Modal';
import { Button } from './Button';

interface MessageModalProps {
    title: string;
    message: string;
    icon?: string;
    onClose: () => void;
    // 角度⑨: エラー時にその場でもう一度同じ操作をやり直せるようにするための再試行ボタン。
    // 渡された場合のみ、OKボタンの隣に表示する。
    onRetry?: () => void;
}

const MessageModal: React.FC<MessageModalProps> = ({ title, message, icon, onClose, onRetry }) => {
    return (
        <Modal
            isOpen={true}
            onClose={onClose}
            title={<span className="text-yellow-400">{title}</span>}
            footer={
                onRetry ? (
                    <>
                        <Button onClick={onClose} variant="secondary" className="flex-1">
                            閉じる
                        </Button>
                        <Button
                            onClick={() => { onRetry(); onClose(); }}
                            variant="primary"
                            className="flex-1"
                        >
                            再試行
                        </Button>
                    </>
                ) : (
                    <Button onClick={onClose} variant="primary" className="w-full">
                        OK
                    </Button>
                )
            }
        >
            <div className="text-center space-y-4">
                {icon && <div className="text-6xl animate-bounce">{icon}</div>}
                <div className="text-lg whitespace-pre-wrap leading-relaxed font-bold">
                    {message}
                </div>
            </div>
        </Modal>
    );
};

export default MessageModal;