import React from 'react';

export default function LevelUpModal({ info, onClose }) {
    if (!info) return null;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm animate-in fade-in duration-300">
            <div className="bg-blue-900 border-4 border-double border-yellow-400 p-8 rounded-xl shadow-2xl text-center max-w-xs w-full animate-bounce-short">
                <div className="text-6xl mb-4 animate-pulse">🎉</div>
                <h2 className="text-2xl font-bold text-yellow-300 mb-2">LEVEL UP!</h2>
                <div className="text-white text-lg mb-4">
                    {info.name}は<br />
                    <span className="text-yellow-300 font-bold">{info.job} Lv.{info.level}</span><br />
                    になった！
                </div>
                <button
                    onClick={onClose}
                    className="bg-red-600 hover:bg-red-500 text-white font-bold py-2 px-6 rounded border-2 border-white"
                >
                    OK
                </button>
            </div>
            <style>{`
        @keyframes bounce-short {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-10px); }
        }
        .animate-bounce-short {
          animation: bounce-short 0.5s ease-in-out 3;
        }
      `}</style>
        </div>
    );
}