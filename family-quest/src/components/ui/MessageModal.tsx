import React from 'react';
import { Modal } from './Modal';
import { Button } from './Button';

interface MessageModalProps {
    title: string;
    message: string;
    icon?: string;
    onClose: () => void;
}

const MessageModal: React.FC<MessageModalProps> = ({ title, message, icon, onClose }) => {
    return (
        <Modal
            isOpen={true}
            onClose={onClose}
            title={<span className="text-yellow-400">{title}</span>}
            footer={
                <Button onClick={onClose} variant="primary" className="w-full">
                    OK
                </Button>
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