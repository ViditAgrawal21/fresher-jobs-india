import { useEffect, useRef, useState } from "react";
import { FreshnessBadge } from "../components/FreshnessBadge";
import { JobCard } from "../components/JobCard";
import { fetchFacets, fetchJobs, type Facets, type Filters, type Job, type Stats } from "../lib/api";
import { JOB_TYPE_LABEL } from "../lib/format";
import { useUrlFilters } from "../lib/useUrlFilters";

const TYPES = ["", "internship", "full_time", "apprenticeship"] as const;
const POSTED = [
  { value: "", label: "Any time" },
  { value: "1", label: "Last 24 hours" },
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "Last 30 days" },
];

function Select({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  children: React.ReactNode;
}) {
  return (
    <label className="relative min-w-0 flex-1 basis-36">
      <span className="sr-only">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`w-full appearance-none rounded-lg border bg-white py-2 pl-3 pr-8 text-sm outline-none focus:border-teal-600 ${
          value ? "border-teal-600/60 text-stone-900" : "border-stone-200 text-stone-600"
        }`}
      >
        {children}
      </select>
      <svg className="pointer-events-none absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" viewBox="0 0 20 20" fill="currentColor" aria-hidden>
        <path d="M5.5 7.5 10 12l4.5-4.5" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" />
      </svg>
    </label>
  );
}

export function JobsPage({ stats }: { stats: Stats | null }) {
  const [filters, update, reset] = useUrlFilters();
  const [query, setQuery] = useState(filters.q);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [items, setItems] = useState<Job[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    fetchFacets().then(setFacets).catch(() => setFacets(null));
  }, []);

  // debounce typing into the URL filter
  useEffect(() => {
    if (query === filters.q) return;
    const t = setTimeout(() => update({ q: query }), 300);
    return () => clearTimeout(t);
  }, [query, filters.q, update]);

  // keep the input in sync when filters change from outside (back button, reset)
  useEffect(() => setQuery(filters.q), [filters.q]);

  useEffect(() => {
    load(filters, 1);
    return () => abortRef.current?.abort();
  }, [filters]);

  function load(f: Filters, p: number) {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true);
    setError(null);
    fetchJobs(f, p, ctrl.signal)
      .then((res) => {
        setItems((prev) => (p === 1 ? res.items : [...prev, ...res.items]));
        setTotal(res.total);
        setPage(p);
      })
      .catch((e) => {
        if (e.name !== "AbortError") setError("Couldn't load jobs. Please try again.");
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
  }

  const hasFilters = Object.values(filters).some(Boolean);

  return (
    <div>
      <section className="mb-5">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Jobs and internships for freshers in India</h1>
        <p className="mt-1.5 text-sm text-stone-600">
          {stats ? `${stats.totals.visible.toLocaleString("en-IN")} open roles` : "Open roles"} for students and fresh graduates,
          collected every morning from public job boards, de-duplicated and tagged by role and city.
        </p>
        <div className="mt-3">
          <FreshnessBadge run={stats?.last_refresh ?? null} />
        </div>
      </section>

      <div className="sticky top-0 z-10 -mx-4 space-y-2 bg-stone-50/95 px-4 py-3 backdrop-blur">
        <label className="relative block">
          <span className="sr-only">Search jobs</span>
          <svg className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" viewBox="0 0 20 20" aria-hidden>
            <circle cx="9" cy="9" r="5.5" stroke="currentColor" strokeWidth="1.6" fill="none" />
            <path d="m13.5 13.5 3 3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search role, skill or company: data analyst, react, Swiggy"
            className="w-full rounded-xl border border-stone-200 bg-white py-2.5 pl-9 pr-3 text-[15px] outline-none placeholder:text-stone-400 focus:border-teal-600"
          />
        </label>

        <div className="flex flex-wrap gap-2">
          <Select label="Role" value={filters.role} onChange={(role) => update({ role })}>
            <option value="">All roles</option>
            {facets?.role.map((r) => (
              <option key={r.value} value={r.value}>
                {r.value} ({r.count})
              </option>
            ))}
          </Select>
          <Select label="City" value={filters.city} onChange={(city) => update({ city })}>
            <option value="">All cities</option>
            {facets?.city
              .filter((c) => c.value !== "Unspecified")
              .map((c) => (
                <option key={c.value} value={c.value}>
                  {c.value} ({c.count})
                </option>
              ))}
          </Select>
          <Select label="Posted" value={filters.days} onChange={(days) => update({ days })}>
            {POSTED.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </Select>
        </div>

        <div className="flex items-center justify-between gap-2">
          <div className="flex gap-1 overflow-x-auto" role="radiogroup" aria-label="Job type">
            {TYPES.map((t) => (
              <button
                key={t || "all"}
                role="radio"
                aria-checked={filters.job_type === t}
                onClick={() => update({ job_type: t })}
                className={`shrink-0 rounded-full px-3 py-1 text-xs font-medium ${
                  filters.job_type === t ? "bg-stone-900 text-white" : "bg-white text-stone-600 ring-1 ring-stone-200 hover:text-stone-900"
                }`}
              >
                {t ? JOB_TYPE_LABEL[t] : "All"}
              </button>
            ))}
          </div>
          {filters.q && (
            <select
              value={filters.sort || "relevance"}
              onChange={(e) => update({ sort: e.target.value as Filters["sort"] })}
              className="shrink-0 rounded-md bg-transparent text-xs text-stone-600 outline-none"
              aria-label="Sort"
            >
              <option value="relevance">Best match</option>
              <option value="newest">Newest</option>
            </select>
          )}
        </div>
      </div>

      <div className="mb-3 mt-2 flex items-center justify-between text-sm text-stone-600">
        <span aria-live="polite">{loading && page === 1 ? "Searching…" : `${total.toLocaleString("en-IN")} jobs`}</span>
        {hasFilters && (
          <button onClick={reset} className="text-teal-700 hover:underline">
            Clear filters
          </button>
        )}
      </div>

      {error && <p className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</p>}

      <div className="space-y-3">
        {items.map((job) => (
          <JobCard key={job.id} job={job} />
        ))}
      </div>

      {!loading && !error && items.length === 0 && (
        <div className="rounded-xl border border-dashed border-stone-300 p-8 text-center text-sm text-stone-600">
          No jobs match these filters. Try a broader search or clear a filter.
        </div>
      )}

      {items.length < total && (
        <button
          onClick={() => load(filters, page + 1)}
          disabled={loading}
          className="mt-4 w-full rounded-xl border border-stone-200 bg-white py-2.5 text-sm font-medium text-stone-700 hover:border-stone-300 disabled:opacity-60"
        >
          {loading ? "Loading…" : `Show more (${(total - items.length).toLocaleString("en-IN")} left)`}
        </button>
      )}
    </div>
  );
}
