import React from 'react';

export default function MessageModal({ title, message, icon, onClose }) {
    if (!message) return null;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm animate-in fade-in duration-300">
            <div className="bg-slate-900 border-4 border-double border-yellow-400 p-8 rounded-xl shadow-2xl text-center max-w-xs w-full animate-bounce-short">
                <div className="text-6xl mb-4 animate-pulse">{icon || '🎁'}</div>
                <h2 className="text-2xl font-bold text-yellow-300 mb-4">{title}</h2>
                <div className="text-white text-lg mb-6 whitespace-pre-wrap leading-relaxed font-bold">
                    {message}
                </div>
                <button
                    onClick={onClose}
                    className="bg-blue-600 hover:bg-blue-500 text-white font-bold py-3 px-10 rounded-full border-2 border-white/20 transition-all hover:scale-105 shadow-lg"
                >
                    OK
                </button>
            </div>
            <style>{`
        @keyframes bounce-short {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-6px); }
        }
        .animate-bounce-short {
          animation: bounce-short 0.4s ease-in-out 2;
        }
      `}</style>
        </div>
    );
}