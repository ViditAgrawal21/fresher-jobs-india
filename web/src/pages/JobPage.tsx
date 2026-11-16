import { useEffect, useState, type ReactNode } from "react";
import { ChevronIcon, ExternalIcon, PinIcon } from "../components/Icons";
import { JobCard } from "../components/JobCard";
import { goBackOr } from "../components/Layout";
import { NotFoundError, fetchJob, type JobDetail } from "../lib/api";
import { JOB_TYPE_LABEL, WORK_MODE_LABEL, descriptionParagraphs, hoursSince, istDateTime, timeAgo } from "../lib/format";

const SOURCE_NAME: Record<string, string> = {
  adzuna: "Adzuna",
  greenhouse: "the company's careers page",
  lever: "the company's careers page",
  ashby: "the company's careers page",
  smartrecruiters: "the company's careers page",
};

function cityList(job: JobDetail): string {
  const cities = job.cities
    .map((c) => (c === "Other" && job.other_city ? job.other_city : c))
    .filter((c) => c !== "Unspecified" && c !== "Other");
  return cities.length ? cities.join(", ") : job.location_raw || "India";
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-4 py-2.5 text-sm">
      <dt className="w-28 shrink-0 text-stone-500">{label}</dt>
      <dd className="min-w-0 text-stone-900">{children}</dd>
    </div>
  );
}

function ShareButton({ job }: { job: JobDetail }) {
  const [copied, setCopied] = useState(false);
  async function share() {
    const url = window.location.href;
    const title = `${job.title} at ${job.company}`;
    if (navigator.share) {
      try {
        await navigator.share({ title, url });
        return;
      } catch {
        /* cancelled: fall through to copy */
      }
    }
    await navigator.clipboard?.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  }
  return (
    <button
      type="button"
      onClick={share}
      className="h-11 rounded-xl border border-stone-200 bg-white px-4 text-sm font-medium text-stone-700 transition-colors hover:border-stone-300 hover:text-stone-900"
    >
      {copied ? "Link copied" : "Share"}
    </button>
  );
}

function ApplyButton({ job, className = "" }: { job: JobDetail; className?: string }) {
  return (
    <a
      href={job.url}
      target="_blank"
      rel="noopener noreferrer"
      className={`inline-flex h-11 items-center justify-center gap-1.5 rounded-xl bg-teal-700 px-5 text-sm font-semibold text-white shadow-[0_1px_2px_rgb(15_118_110/0.3)] transition-colors hover:bg-teal-800 ${className}`}
    >
      Apply on {job.source === "adzuna" ? "Adzuna" : job.company}
      <ExternalIcon className="h-4 w-4" />
    </a>
  );
}

function Skeleton() {
  return (
    <div className="animate-pulse" aria-hidden>
      <div className="h-7 w-4/5 rounded bg-stone-200/80" />
      <div className="mt-3 h-4 w-1/3 rounded bg-stone-200/70" />
      <div className="mt-5 flex gap-2">
        <div className="h-6 w-24 rounded-md bg-stone-200/70" />
        <div className="h-6 w-36 rounded-md bg-stone-200/60" />
      </div>
      <div className="mt-6 h-11 w-48 rounded-xl bg-stone-200/80" />
      <div className="mt-8 space-y-2">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="h-3.5 rounded bg-stone-200/60" style={{ width: `${95 - i * 7}%` }} />
        ))}
      </div>
    </div>
  );
}

export function JobPage({ id }: { id: number }) {
  const [job, setJob] = useState<JobDetail | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "missing" | "error">("loading");

  useEffect(() => {
    const ctrl = new AbortController();
    setState("loading");
    fetchJob(id, ctrl.signal)
      .then((j) => {
        setJob(j);
        setState("ok");
        document.title = `${j.title} at ${j.company} · Fresher Jobs India`;
      })
      .catch((e) => {
        if (e.name === "AbortError") return;
        setState(e instanceof NotFoundError ? "missing" : "error");
      });
    return () => {
      ctrl.abort();
      document.title = "Fresher Jobs India · jobs & internships for freshers, updated daily";
    };
  }, [id]);

  const back = (
    <button
      type="button"
      onClick={() => goBackOr("/")}
      className="mb-5 inline-flex items-center gap-1 rounded-md text-sm text-stone-500 transition-colors hover:text-stone-900"
    >
      <ChevronIcon className="h-4 w-4 rotate-90" />
      All jobs
    </button>
  );

  if (state === "loading") return <div>{back}<Skeleton /></div>;

  if (state !== "ok" || !job) {
    return (
      <div>
        {back}
        <div className="rounded-xl border border-dashed border-stone-300 bg-white/50 px-6 py-12 text-center">
          <p className="font-medium text-stone-900">{state === "missing" ? "This job isn't on the site any more" : "Couldn't load this job"}</p>
          <p className="mx-auto mt-1 max-w-[44ch] text-sm text-stone-600">
            {state === "missing"
              ? "It may have closed, or it was removed when the list refreshed this morning."
              : "Check your connection and try again."}
          </p>
          <button type="button" onClick={() => goBackOr("/")} className="mt-4 rounded-lg bg-stone-900 px-4 py-2 text-sm font-medium text-white hover:bg-teal-700">
            Browse open jobs
          </button>
        </div>
      </div>
    );
  }

  const isNew = hoursSince(job.posted_at) < 48;
  const mode = WORK_MODE_LABEL[job.work_mode];
  const where = SOURCE_NAME[job.source] ?? job.source;

  return (
    <div className="pb-20 sm:pb-0">
      {back}

      {!job.listed && (
        <p className="mb-5 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-900 ring-1 ring-inset ring-amber-600/20">
          This post is no longer in the main list. It may have closed or been hidden after a refresh, so check the original before applying.
        </p>
      )}

      <article>
        <header>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-medium text-stone-500">{job.company}</span>
            {isNew && (
              <span className="rounded-full bg-emerald-50 px-2 py-0.5 font-semibold text-emerald-700 ring-1 ring-inset ring-emerald-600/20">New</span>
            )}
          </div>
          <h1 className="mt-1.5 text-2xl font-semibold leading-tight tracking-tight sm:text-[28px]">{job.title}</h1>
          <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-stone-600">
            <span className="inline-flex items-center gap-1">
              <PinIcon className="h-4 w-4 text-stone-400" />
              {cityList(job)}
            </span>
            <span className="text-stone-300" aria-hidden>·</span>
            <time dateTime={job.posted_at}>Posted {timeAgo(job.posted_at)}</time>
          </p>
          <div className="mt-4 flex flex-wrap gap-1.5">
            <span className="rounded-md bg-teal-50 px-2 py-0.5 text-xs font-medium text-teal-800 ring-1 ring-inset ring-teal-600/15">
              {JOB_TYPE_LABEL[job.job_type]}
            </span>
            <span className="rounded-md bg-stone-100 px-2 py-0.5 text-xs text-stone-700">{job.role}</span>
            {mode && <span className="rounded-md bg-stone-100 px-2 py-0.5 text-xs text-stone-700">{mode}</span>}
            <span className="rounded-md bg-stone-100 px-2 py-0.5 text-xs text-stone-700">Open to freshers</span>
          </div>

          <div className="mt-6 hidden items-center gap-2 sm:flex">
            <ApplyButton job={job} />
            <ShareButton job={job} />
          </div>
          <p className="mt-2 hidden text-xs text-stone-500 sm:block">You'll finish applying on {where}.</p>
        </header>

        <section className="mt-8">
          <h2 className="text-sm font-semibold text-stone-900">About the role</h2>
          {job.description ? (
            <div className="mt-2 max-w-[68ch] space-y-3 text-[15px] leading-relaxed text-stone-700">
              {descriptionParagraphs(job.description).map((p, i) => (
                <p key={i}>
                  {p.label && <span className="block text-sm font-semibold text-stone-900">{p.label}</span>}
                  {p.body}
                </p>
              ))}
              <p className="text-xs text-stone-500">Preview of the original post, shortened.</p>
            </div>
          ) : (
            <p className="mt-2 text-sm text-stone-500">No description was shared with this listing.</p>
          )}
          <a
            href={job.url}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-teal-700 underline-offset-2 hover:underline"
          >
            Read the full post on {job.source === "adzuna" ? "Adzuna" : `${job.company}'s site`}
            <ExternalIcon className="h-3.5 w-3.5" />
          </a>
        </section>

        <section className="mt-8">
          <h2 className="text-sm font-semibold text-stone-900">At a glance</h2>
          <dl className="mt-2 divide-y divide-stone-200 rounded-xl border border-stone-200 bg-white px-4">
            <Row label="Role">{job.role}</Row>
            <Row label="Location">
              {cityList(job)}
              {job.location_raw && job.location_raw !== cityList(job) && <span className="text-stone-500"> (listed as “{job.location_raw}”)</span>}
            </Row>
            <Row label="Job type">{JOB_TYPE_LABEL[job.job_type]}</Row>
            {mode && <Row label="Work mode">{mode}</Row>}
            <Row label="Source">
              {job.source === "adzuna" ? (
                <a href="https://www.adzuna.in" target="_blank" rel="noopener" className="underline underline-offset-2">Jobs by Adzuna</a>
              ) : (
                "Company careers page"
              )}
              {job.also_on.length > 0 && (
                <span className="text-stone-500">
                  {" "}· also on{" "}
                  {job.also_on.map((o, i) => (
                    <span key={o.url}>
                      {i > 0 && ", "}
                      <a href={o.url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2">
                        {o.source === "adzuna" ? "Adzuna" : o.source}
                      </a>
                    </span>
                  ))}
                </span>
              )}
            </Row>
            <Row label="Last checked">{istDateTime(job.last_seen_at)} IST</Row>
          </dl>
          <p className="mt-2 text-xs text-stone-500">Role, location, job type and "open to freshers" are tagged by an AI model and can be wrong. The original post is the source of truth.</p>
        </section>

        {job.similar.length > 0 && (
          <section className="mt-10">
            <h2 className="text-sm font-semibold text-stone-900">Similar jobs</h2>
            <div className="mt-3 space-y-3">
              {job.similar.map((s) => (
                <JobCard key={s.id} job={s} />
              ))}
            </div>
          </section>
        )}
      </article>

      {/* On phones the apply button stays in reach while you read. */}
      <div className="fixed inset-x-0 bottom-0 z-30 flex gap-2 border-t border-stone-200 bg-white/95 px-4 py-3 backdrop-blur sm:hidden">
        <ApplyButton job={job} className="flex-1" />
        <ShareButton job={job} />
      </div>
    </div>
  );
}
