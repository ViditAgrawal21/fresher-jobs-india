import type { Job } from "../lib/api";
import { JOB_TYPE_LABEL, SOURCE_LABEL, WORK_MODE_LABEL, hoursSince, timeAgo } from "../lib/format";
import { ExternalIcon, PinIcon } from "./Icons";

// Internship vs job is the first thing a student filters on, so it gets its own colour.
const TYPE_STYLE: Record<Job["job_type"], string> = {
  internship: "bg-violet-50 text-violet-700 ring-violet-600/15",
  full_time: "bg-sky-50 text-sky-700 ring-sky-600/15",
  apprenticeship: "bg-amber-50 text-amber-800 ring-amber-600/20",
  contract: "bg-stone-100 text-stone-700 ring-stone-500/15",
};

function placeLabel(job: Job): string {
  const cities = job.cities
    .map((c) => (c === "Other" && job.other_city ? job.other_city : c))
    .filter((c) => c !== "Unspecified" && c !== "Other");
  if (cities.length === 0) return "India";
  if (cities.length <= 3) return cities.join(", ");
  return `${cities.slice(0, 3).join(", ")} +${cities.length - 3} more`;
}

export function JobCard({ job }: { job: Job }) {
  const mode = WORK_MODE_LABEL[job.work_mode];
  const isNew = hoursSince(job.posted_at) < 48;
  const alsoOn = [...new Set(job.also_on.map((s) => SOURCE_LABEL[s] ?? s))].filter((s) => s !== SOURCE_LABEL[job.source]);

  return (
    <article className="group relative rounded-xl border border-stone-200 bg-white p-4 transition-[border-color,box-shadow] duration-150 hover:border-stone-300 hover:shadow-[0_2px_8px_-2px_rgb(28_25_23/0.08)] sm:p-5">
      <div className="flex items-start justify-between gap-3">
        <h3 className="line-clamp-2 text-[15px] font-semibold leading-snug text-stone-900 sm:text-base">
          {/* Stretched link: the whole card opens the post, which is what you want on a phone. */}
          <a
            href={job.url}
            target="_blank"
            rel="noopener noreferrer"
            className="after:absolute after:inset-0 after:rounded-xl group-hover:text-teal-800"
          >
            {job.title}
            <span className="sr-only"> at {job.company} (opens the original post in a new tab)</span>
          </a>
        </h3>
        {isNew ? (
          <span className="mt-0.5 shrink-0 rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 ring-1 ring-inset ring-emerald-600/20">
            New
          </span>
        ) : (
          <time className="mt-0.5 shrink-0 text-xs text-stone-500" dateTime={job.posted_at}>
            {timeAgo(job.posted_at)}
          </time>
        )}
      </div>

      <p className="mt-1 text-sm font-medium text-stone-700">{job.company}</p>
      <p className="mt-0.5 flex items-center gap-1 text-sm text-stone-500">
        <PinIcon className="h-3.5 w-3.5 text-stone-400" />
        <span className="truncate">
          {placeLabel(job)}
          {mode && mode !== "On-site" && <> · {mode}</>}
        </span>
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <span className={`rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TYPE_STYLE[job.job_type]}`}>
          {JOB_TYPE_LABEL[job.job_type]}
        </span>
        <span className="rounded-md bg-stone-100 px-2 py-0.5 text-xs text-stone-700">{job.role}</span>
      </div>

      <div className="mt-3 flex items-center justify-between gap-3 border-t border-stone-100 pt-3">
        <p className="min-w-0 truncate text-xs text-stone-500">
          {job.source === "adzuna" ? (
            <a
              href="https://www.adzuna.in"
              target="_blank"
              rel="noopener"
              className="relative z-10 font-medium text-stone-600 underline-offset-2 hover:underline"
            >
              Jobs by Adzuna
            </a>
          ) : (
            <>via {SOURCE_LABEL[job.source] ?? job.source}</>
          )}
          {alsoOn.length > 0 && <> · also on {alsoOn.join(", ")}</>}
          {isNew && <> · {timeAgo(job.posted_at)}</>}
        </p>
        <span
          aria-hidden
          className="inline-flex shrink-0 items-center gap-1 rounded-lg bg-stone-900 px-3 py-1.5 text-xs font-medium text-white transition-colors group-hover:bg-teal-700"
        >
          Apply
          <ExternalIcon className="h-3 w-3" />
        </span>
      </div>
    </article>
  );
}

export function JobCardSkeleton() {
  return (
    <div className="rounded-xl border border-stone-200 bg-white p-4 sm:p-5" aria-hidden>
      <div className="animate-pulse">
        <div className="h-4 w-3/4 rounded bg-stone-200/80" />
        <div className="mt-2.5 h-3.5 w-1/3 rounded bg-stone-200/70" />
        <div className="mt-2 h-3.5 w-1/2 rounded bg-stone-200/60" />
        <div className="mt-4 flex gap-1.5">
          <div className="h-5 w-20 rounded-md bg-stone-200/70" />
          <div className="h-5 w-32 rounded-md bg-stone-200/60" />
        </div>
        <div className="mt-4 flex items-center justify-between border-t border-stone-100 pt-3">
          <div className="h-3 w-28 rounded bg-stone-200/60" />
          <div className="h-7 w-16 rounded-lg bg-stone-200/80" />
        </div>
      </div>
    </div>
  );
}
