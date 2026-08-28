import { ReactNode } from "react";

type ModalProps = {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
};

export function Modal({ open, onClose, title, children }: ModalProps) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 sm:items-center sm:px-4">
      <div className="w-full max-w-3xl max-h-[85vh] overflow-y-auto rounded-t-2xl border border-border bg-surface shadow-2xl pb-[env(safe-area-inset-bottom)] sm:rounded-xl">
        <div className="mx-auto mt-2 h-1 w-10 rounded-full bg-border sm:hidden" />
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h3 className="font-semibold">{title}</h3>
          <button onClick={onClose} className="px-2 py-1 text-muted hover:text-text">
            ✕
          </button>
        </div>
        <div className="space-y-3 p-4">{children}</div>
      </div>
    </div>
  );
}
