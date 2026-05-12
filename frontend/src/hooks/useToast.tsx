import { createContext, useCallback, useContext, useState } from "react";
import type { ReactNode } from "react";

type Tone = "info" | "success" | "error";

interface Toast {
  id: number;
  message: string;
  tone: Tone;
}

interface ToastContextValue {
  push: (message: string, tone?: Tone) => void;
}

const ToastContext = createContext<ToastContextValue>({
  push: () => undefined,
});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((message: string, tone: Tone = "info") => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, message, tone }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4000);
  }, []);
  return (
    <ToastContext.Provider value={{ push }}>
      {children}
      <div className="fixed bottom-20 inset-x-0 z-40 flex justify-center pointer-events-none">
        <div className="flex flex-col gap-2 items-center">
          {toasts.map((t) => (
            <div
              key={t.id}
              className={`pointer-events-auto rounded-xl px-4 py-2 text-sm shadow-lg ${
                t.tone === "success"
                  ? "bg-green-500/15 text-green-300 border border-green-500/30"
                  : t.tone === "error"
                    ? "bg-red-500/15 text-red-300 border border-red-500/30"
                    : "bg-card text-gray-200 border border-white/5"
              }`}
            >
              {t.message}
            </div>
          ))}
        </div>
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
