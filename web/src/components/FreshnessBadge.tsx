import type { Run } from "../lib/api";
import { hoursSince, istDateTime } from "../lib/format";

/** Green when the daily job ran in the last 26h, amber when it looks like it missed a day. */
export function FreshnessBadge({ run }: { run: Run | null }) {
  if (!run?.finished_at) return null;
  const stale = hoursSince(run.finished_at) > 26;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs ${
        stale ? "bg-amber-50 text-amber-800" : "bg-emerald-50 text-emerald-800"
      }`}
      title="The list refreshes automatically every morning at 6:00 IST"
    >
      <span className={`h-1.5 w-1.5 rounded-full ${stale ? "bg-amber-500" : "bg-emerald-500"}`} />
      {stale ? "Last refreshed" : "Refreshed"} {istDateTime(run.finished_at)} IST
    </span>
  );
}
