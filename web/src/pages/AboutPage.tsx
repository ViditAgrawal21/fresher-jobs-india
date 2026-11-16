import type { ReactNode } from "react";
import type { Stats } from "../lib/api";
import { istDateTime } from "../lib/format";

const STEPS: { title: string; body: string }[] = [
  { title: "Collect", body: "Every day at 6:00 IST, Cloud Scheduler starts a Cloud Run job that calls the Adzuna India API and the public job-board APIs of 27 companies (Greenhouse, Lever, Ashby, SmartRecruiters)." },
  { title: "Filter", body: "Rules drop non-India and obviously senior posts before anything costs money." },
  { title: "Normalize", body: "HTML stripped, URLs cleaned, company names and city spellings normalized (Bangalore → Bengaluru)." },
  { title: "De-duplicate", body: "Same URL, same company + title + city, or near-identical titles at the same company and city. Copies are marked, not deleted; the company's own posting wins over an aggregator's." },
  { title: "Tag", body: "Only new posts go to Claude Haiku 5.5, 20 per request, with output forced into a fixed schema: role, cities, work mode, job type and whether freshers can apply." },
  { title: "Serve", body: "Postgres (Neon) full-text + trigram search behind a FastAPI endpoint, and this page." },
];

function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="rounded-xl border border-stone-200 bg-white p-4">
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      <div className="mt-0.5 text-xs text-stone-600">{label}</div>
      {hint && <div className="mt-1 text-[11px] text-stone-400">{hint}</div>}
    </div>
  );
}

const pct = (x: number) => `${Math.round(x * 100)}%`;

export function AboutPage({ stats }: { stats: Stats | null }) {
  const t = stats?.totals;
  const ev = stats?.eval;
  return (
    <div className="space-y-10">
      <section>
        <h1 className="text-2xl font-semibold tracking-tight">How it works</h1>
        <p className="mt-2 text-sm leading-relaxed text-stone-600">
          A small daily pipeline that turns public job listings into one clean, searchable list for freshers.
          Only sources whose terms allow it are used: official APIs and companies' own public job boards.
          No scraping of LinkedIn, Naukri, Indeed or Internshala.
        </p>
        <ol className="mt-5 space-y-3">
          {STEPS.map((s, i) => (
            <li key={s.title} className="flex gap-3">
              <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-teal-700 text-xs font-semibold text-white">
                {i + 1}
              </span>
              <div>
                <div className="text-sm font-medium">{s.title}</div>
                <p className="text-sm text-stone-600">{s.body}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section>
        <h2 className="text-lg font-semibold">Right now</h2>
        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="fresher roles live" value={t?.visible.toLocaleString("en-IN") ?? "–"} />
          <Stat label="posts collected (active)" value={t?.active.toLocaleString("en-IN") ?? "–"} />
          <Stat label="duplicates hidden" value={t?.duplicates.toLocaleString("en-IN") ?? "–"} />
          <Stat label="not for freshers" value={t?.not_fresher.toLocaleString("en-IN") ?? "–"} hint={t ? `tagged, then hidden; plus ${t.outside_india} outside India` : undefined} />
        </div>
      </section>

      <section>
        <h2 className="text-lg font-semibold">Accuracy and cost</h2>
        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Stat
            label="role tag accuracy"
            value={ev ? pct(ev.role_accuracy) : "–"}
            hint={ev ? `${ev.n} hand-checked posts, 95% CI ${pct(ev.role_ci[0])}–${pct(ev.role_ci[1])}` : "hand-check pending"}
          />
          <Stat
            label="city tag accuracy"
            value={ev ? pct(ev.city_accuracy) : "–"}
            hint={ev ? `95% CI ${pct(ev.city_ci[0])}–${pct(ev.city_ci[1])}` : "hand-check pending"}
          />
          <Stat
            label="LLM cost per 1,000 posts"
            value={stats?.cost.per_1000_posts_usd != null ? `$${stats.cost.per_1000_posts_usd.toFixed(3)}` : "–"}
            hint={stats ? `from real token counts, ${stats.cost.posts_tagged.toLocaleString("en-IN")} posts tagged so far` : undefined}
          />
        </div>
      </section>

      <section>
        <h2 className="text-lg font-semibold">Recent runs</h2>
        <div className="mt-3 overflow-x-auto rounded-xl border border-stone-200 bg-white">
          <table className="w-full min-w-[520px] text-left text-sm">
            <thead className="border-b border-stone-200 text-xs text-stone-500">
              <tr>
                <th className="px-3 py-2 font-medium">Started (IST)</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 text-right font-medium">New</th>
                <th className="px-3 py-2 text-right font-medium">Duplicates</th>
                <th className="px-3 py-2 text-right font-medium">Tagged</th>
                <th className="px-3 py-2 text-right font-medium">LLM cost</th>
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {stats?.runs.map((r) => (
                <tr key={r.id} className="border-b border-stone-100 last:border-0">
                  <td className="px-3 py-2">{istDateTime(r.started_at)}</td>
                  <td className="px-3 py-2">
                    <span
                      className={
                        r.status === "ok" ? "text-emerald-700" : r.status === "partial" ? "text-amber-700" : r.status === "failed" ? "text-red-700" : "text-stone-500"
                      }
                      title={r.errors?.map((e) => `${e.source}: ${e.error}`).join("\n")}
                    >
                      {r.status}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right">{r.stats.new ?? "–"}</td>
                  <td className="px-3 py-2 text-right">{r.stats.duplicates ?? "–"}</td>
                  <td className="px-3 py-2 text-right">{r.stats.tagged ?? "–"}</td>
                  <td className="px-3 py-2 text-right">
                    {typeof r.stats.llm_cost_usd === "number" ? `$${r.stats.llm_cost_usd.toFixed(4)}` : "–"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
