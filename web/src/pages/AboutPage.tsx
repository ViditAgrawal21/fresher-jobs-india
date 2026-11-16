import type { ReactNode } from "react";
import { REPO_URL } from "../components/Layout";
import { ExternalIcon } from "../components/Icons";
import type { Run, Stats } from "../lib/api";
import { duration, istDateTime } from "../lib/format";

const STEPS: { title: string; body: string; tech: string }[] = [
  { title: "Schedule", body: "A scheduler starts the pipeline every morning at 6:00 IST. Nobody presses a button.", tech: "Cloud Scheduler → Cloud Run job" },
  { title: "Collect", body: "Adzuna's India API, Jooble's API, and the public job-board APIs of 27 companies, all fetched in parallel with retries.", tech: "Python · httpx" },
  { title: "Filter", body: "Cheap rules drop clearly senior titles and non-India locations before anything costs money.", tech: "Rules" },
  { title: "De-duplicate", body: "Same link, same company + title + city, or near-identical titles at the same company. Copies are kept but hidden, and the company's own post wins.", tech: "rapidfuzz · union-find" },
  { title: "Tag", body: "Only new posts go to the LLM, 20 per request. Answers are forced into a fixed schema: role, cities, job type, work mode, in India, open to freshers.", tech: "Claude Haiku 5.5" },
  { title: "Serve", body: "Full-text and typo-tolerant search over Postgres, behind a small API. This site is static and calls it through one domain.", tech: "Neon Postgres · FastAPI · Vercel" },
];

function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="border-t border-stone-200 pt-8">
      <div className="mb-4 flex items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

function Funnel({ stats }: { stats: Stats }) {
  const t = stats.totals;
  const parts = [
    { key: "live", label: "Shown on the site", n: t.visible, color: "bg-teal-600", note: "open to freshers, in India" },
    { key: "nf", label: "Not for freshers", n: t.not_fresher, color: "bg-stone-300", note: "the LLM read “2+ years” or a senior role" },
    { key: "dup", label: "Duplicates", n: t.duplicates, color: "bg-stone-400", note: "same job on another board or posted twice" },
    { key: "abroad", label: "Located abroad", n: t.outside_india, color: "bg-amber-400", note: "listed as “India”, job is overseas" },
    { key: "un", label: "Waiting for tags", n: t.untagged, color: "bg-sky-300", note: "tagged on the next run" },
  ].filter((p) => p.n > 0);
  const total = t.active || 1;

  return (
    <div>
      <p className="text-sm text-stone-600">
        Of <span className="font-semibold tabular-nums text-stone-900">{t.active.toLocaleString("en-IN")}</span> posts collected and still open,{" "}
        <span className="font-semibold tabular-nums text-teal-700">{t.visible.toLocaleString("en-IN")}</span> make it onto the site.
      </p>
      <div className="mt-3 flex h-3 w-full overflow-hidden rounded-full bg-stone-100" role="img" aria-label="How collected posts are split">
        {parts.map((p) => (
          <div key={p.key} className={`${p.color} h-full border-r-2 border-white last:border-r-0`} style={{ width: `${(p.n / total) * 100}%` }} />
        ))}
      </div>
      <dl className="mt-4 grid gap-x-6 gap-y-2.5 sm:grid-cols-2">
        {parts.map((p) => (
          <div key={p.key} className="flex items-start gap-2.5">
            <span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-sm ${p.color}`} aria-hidden />
            <div className="min-w-0">
              <dt className="text-sm">
                <span className="font-semibold tabular-nums">{p.n.toLocaleString("en-IN")}</span> <span className="text-stone-700">{p.label}</span>
              </dt>
              <dd className="text-xs text-stone-500">{p.note}</dd>
            </div>
          </div>
        ))}
      </dl>
    </div>
  );
}

function Pipeline() {
  return (
    <ol className="relative">
      {STEPS.map((s, i) => (
        <li key={s.title} className="relative flex gap-4 pb-6 last:pb-0">
          {i < STEPS.length - 1 && <span className="absolute left-[13px] top-7 h-[calc(100%-1.75rem)] w-px bg-stone-200" aria-hidden />}
          <span className="relative z-10 grid h-7 w-7 shrink-0 place-items-center rounded-full border border-stone-200 bg-white text-xs font-semibold tabular-nums text-stone-700">
            {i + 1}
          </span>
          <div className="min-w-0 pt-0.5">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
              <h3 className="text-sm font-semibold">{s.title}</h3>
              <span className="text-xs text-teal-700">{s.tech}</span>
            </div>
            <p className="mt-1 text-sm leading-relaxed text-stone-600">{s.body}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

const pct = (x: number) => `${Math.round(x * 100)}%`;

function Metric({ label, value, note }: { label: string; value: ReactNode; note: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 py-3 sm:flex-row sm:items-baseline sm:gap-4">
      <dt className="text-sm text-stone-600 sm:w-56 sm:shrink-0">{label}</dt>
      <dd>
        <span className="text-base font-semibold tabular-nums">{value}</span>
        <span className="ml-2 text-xs text-stone-500">{note}</span>
      </dd>
    </div>
  );
}

function StatusLabel({ status, errors }: { status: Run["status"]; errors?: Run["errors"] }) {
  const tone = {
    ok: "bg-emerald-500 text-emerald-700",
    partial: "bg-amber-500 text-amber-800",
    failed: "bg-red-500 text-red-700",
    running: "bg-sky-500 text-sky-700",
  }[status];
  const [dot, text] = tone.split(" ");
  return (
    <span className={`inline-flex items-center gap-1.5 ${text}`} title={errors?.map((e) => `${e.source}: ${e.error}`).join("\n") || undefined}>
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} aria-hidden />
      {status}
    </span>
  );
}

export function AboutPage({ stats }: { stats: Stats | null }) {
  const ev = stats?.eval;
  const cost = stats?.cost;

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-[26px] font-semibold leading-tight tracking-tight sm:text-[32px]">How it works</h1>
        <p className="mt-2 max-w-[62ch] text-[15px] leading-relaxed text-stone-600">
          A small pipeline that runs every morning and turns scattered public listings into one clean list for students and fresh graduates.
          It only uses sources whose terms allow it: official APIs and companies' own job boards.
        </p>
      </header>

      {stats && (
        <Section title="Today's numbers">
          <Funnel stats={stats} />
        </Section>
      )}

      <Section title="The pipeline">
        <Pipeline />
      </Section>

      <Section title="Quality and cost">
        <dl className="divide-y divide-stone-200 rounded-xl border border-stone-200 bg-white px-4">
          <Metric
            label="Role tag accuracy"
            value={ev ? pct(ev.role_accuracy) : "Hand-check in progress"}
            note={ev ? `${ev.n} posts checked by hand, 95% CI ${pct(ev.role_ci[0])}–${pct(ev.role_ci[1])}` : "50 posts, labelled blind"}
          />
          <Metric
            label="City tag accuracy"
            value={ev ? pct(ev.city_accuracy) : "Hand-check in progress"}
            note={ev ? `exact match on every city, 95% CI ${pct(ev.city_ci[0])}–${pct(ev.city_ci[1])}` : "exact match on every city"}
          />
          <Metric
            label="LLM cost per 1,000 posts"
            value={cost?.per_1000_posts_usd != null ? `$${cost.per_1000_posts_usd.toFixed(3)}` : "–"}
            note={cost ? `measured from token counts, ${cost.posts_tagged.toLocaleString("en-IN")} posts tagged so far` : ""}
          />
          <Metric label="Hosting cost" value="≈ $0 / month" note="Cloud Run, Cloud Scheduler, Neon and Vercel free tiers" />
        </dl>
      </Section>

      <Section title="Sources">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <h3 className="text-sm font-medium text-stone-900">Used</h3>
            <ul className="mt-2 space-y-1.5 text-sm text-stone-600">
              <li>Adzuna India: official API, credited on every listing</li>
              <li>Jooble: official API</li>
              <li>Greenhouse, Lever, Ashby and SmartRecruiters: the public job-board APIs that 27 companies (Bosch, Swiggy, Razorpay, CRED and others) use for their own career pages</li>
            </ul>
          </div>
          <div>
            <h3 className="text-sm font-medium text-stone-900">Not used, on purpose</h3>
            <p className="mt-2 text-sm text-stone-600">LinkedIn, Naukri, Indeed, Internshala, Glassdoor. Their terms don't allow automated collection.</p>
          </div>
        </div>
      </Section>

      <Section
        title="Recent runs"
        aside={
          <a
            href={`${REPO_URL}/blob/main/docs/BUILD_LOG.md`}
            target="_blank"
            rel="noopener"
            className="inline-flex items-center gap-1 text-sm font-medium text-teal-700 underline-offset-2 hover:underline"
          >
            What broke while building it
            <ExternalIcon className="h-3.5 w-3.5" />
          </a>
        }
      >
        <div className="overflow-x-auto rounded-xl border border-stone-200 bg-white">
          <table className="w-full min-w-[560px] text-left text-sm">
            <thead className="border-b border-stone-200 text-xs text-stone-500">
              <tr>
                <th className="px-3 py-2 font-medium">Started (IST)</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 text-right font-medium">Took</th>
                <th className="px-3 py-2 text-right font-medium">New posts</th>
                <th className="px-3 py-2 text-right font-medium">Tagged</th>
                <th className="px-3 py-2 text-right font-medium">LLM cost</th>
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {stats?.runs.map((r) => (
                <tr key={r.id} className="border-b border-stone-100 last:border-0">
                  <td className="whitespace-nowrap px-3 py-2">{istDateTime(r.started_at)}</td>
                  <td className="px-3 py-2">
                    <StatusLabel status={r.status} errors={r.errors} />
                  </td>
                  <td className="px-3 py-2 text-right text-stone-600">{duration(r.started_at, r.finished_at)}</td>
                  <td className="px-3 py-2 text-right">{r.stats.new ?? "–"}</td>
                  <td className="px-3 py-2 text-right">{r.stats.tagged ?? "–"}</td>
                  <td className="px-3 py-2 text-right">{typeof r.stats.llm_cost_usd === "number" ? `$${r.stats.llm_cost_usd.toFixed(3)}` : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-stone-500">Hover a status to see what went wrong in a partial or failed run.</p>
      </Section>
    </div>
  );
}
