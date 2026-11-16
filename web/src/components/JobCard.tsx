import type { Job } from "../lib/api";
import { JOB_TYPE_LABEL, SOURCE_LABEL, WORK_MODE_LABEL, timeAgo } from "../lib/format";

function placeLabel(job: Job): string {
  const cities = job.cities.map((c) => (c === "Other" && job.other_city ? job.other_city : c));
  const shown = cities.filter((c) => c !== "Unspecified");
  if (shown.length === 0) return "India";
  if (shown.length <= 3) return shown.join(", ");
  return `${shown.slice(0, 3).join(", ")} +${shown.length - 3}`;
}

export function JobCard({ job }: { job: Job }) {
  const mode = WORK_MODE_LABEL[job.work_mode];
  const alsoOn = [...new Set(job.also_on.map((s) => SOURCE_LABEL[s] ?? s))].filter(
    (s) => s !== SOURCE_LABEL[job.source],
  );

  return (
    <article className="group rounded-xl border border-stone-200 bg-white p-4 transition-colors hover:border-teal-600/40 sm:p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-[15px] font-semibold leading-snug text-stone-900 sm:text-base">
            <a href={job.url} target="_blank" rel="noopener noreferrer" className="hover:text-teal-700">
              {job.title}
            </a>
          </h3>
          <p className="mt-0.5 truncate text-sm text-stone-600">
            {job.company} <span className="text-stone-400">·</span> {placeLabel(job)}
          </p>
        </div>
        <time className="shrink-0 pt-0.5 text-xs text-stone-500" dateTime={job.posted_at}>
          {timeAgo(job.posted_at)}
        </time>
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        <span className="rounded-md bg-teal-50 px-2 py-0.5 text-xs font-medium text-teal-800">{job.role}</span>
        <span className="rounded-md bg-stone-100 px-2 py-0.5 text-xs text-stone-700">{JOB_TYPE_LABEL[job.job_type]}</span>
        {mode && <span className="rounded-md bg-stone-100 px-2 py-0.5 text-xs text-stone-700">{mode}</span>}
      </div>

      <div className="mt-3 flex items-center justify-between gap-3 border-t border-stone-100 pt-3">
        <p className="min-w-0 truncate text-xs text-stone-500">
          {job.source === "adzuna" ? (
            <a href="https://www.adzuna.in" target="_blank" rel="noopener" className="font-medium text-stone-600 hover:underline">
              Jobs by Adzuna
            </a>
          ) : (
            <>via {SOURCE_LABEL[job.source] ?? job.source}</>
          )}
          {alsoOn.length > 0 && <> · also on {alsoOn.join(", ")}</>}
        </p>
        <a
          href={job.url}
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0 rounded-lg bg-stone-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-teal-700"
        >
          Apply
        </a>
      </div>
    </article>
  );
}
