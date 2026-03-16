import { FC } from "react";

type Props = {
  lastUpdated?: number;
  onRefresh?: () => void;
  onClear?: () => Promise<void> | void;
  isLoading?: boolean;
};

export const SignalHeader: FC<Props> = ({ lastUpdated, onRefresh, onClear, isLoading }) => {
  const updatedText = lastUpdated ? new Date(lastUpdated).toLocaleTimeString() : "–";
  return (
    <div className="flex items-center justify-between mb-3">
      <div>
        <h2 className="font-semibold">Latest Signals</h2>
        <p className="text-xs text-muted">Last updated: {updatedText}</p>
      </div>
      <div className="flex gap-2">
        <button
          onClick={onRefresh}
          className="px-3 py-1 rounded-lg border border-border text-sm hover:border-primary transition"
          disabled={isLoading}
        >
          Refresh
        </button>
        <button
          onClick={onClear}
          className="px-3 py-1 rounded-lg border border-danger text-sm text-danger hover:bg-danger/10 transition"
          disabled={isLoading}
        >
          Clear
        </button>
      </div>
    </div>
  );
};
