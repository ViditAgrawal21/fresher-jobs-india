import { useEffect, useRef, useState, type ReactNode } from "react";
import { AlertIcon, ChevronIcon, SearchIcon, XIcon } from "../components/Icons";
import { JobCard, JobCardSkeleton } from "../components/JobCard";
import { fetchFacets, fetchJobs, type Facets, type Filters, type Job, type Stats } from "../lib/api";
import { JOB_TYPE_LABEL, hoursSince, istTime } from "../lib/format";
import { useUrlFilters } from "../lib/useUrlFilters";

const TYPES = ["", "internship", "full_time", "apprenticeship"] as const;
const TYPE_CHIP: Record<string, string> = { "": "All jobs", internship: "Internships", full_time: "Full-time jobs", apprenticeship: "Apprenticeships" };
const POSTED: Record<string, string> = { "": "Any time", "1": "Last 24 hours", "7": "Last 7 days", "30": "Last 30 days" };
const NON_CITIES = new Set(["Unspecified", "Other", "Pan India"]);

function Select({ label, value, onChange, children }: { label: string; value: string; onChange: (v: string) => void; children: ReactNode }) {
  return (
    <label className="relative block min-w-0">
      <span className="sr-only">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`h-10 w-full appearance-none truncate rounded-lg border bg-white pl-3 pr-8 text-sm outline-none transition-[border-color,box-shadow] duration-150 focus:border-teal-600 focus:ring-4 focus:ring-teal-600/15 ${
          value ? "border-teal-600/50 font-medium text-stone-900" : "border-stone-200 text-stone-600 hover:border-stone-300"
        }`}
      >
        {children}
      </select>
      <ChevronIcon className="pointer-events-none absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
    </label>
  );
}

function Chip({ active, onClick, children, ...rest }: { active: boolean; onClick: () => void; children: ReactNode } & React.AriaAttributes & { role?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      {...rest}
      className={`h-8 shrink-0 rounded-full px-3 text-[13px] font-medium transition-colors duration-150 ${
        active ? "bg-stone-900 text-white" : "bg-white text-stone-600 ring-1 ring-inset ring-stone-200 hover:text-stone-900 hover:ring-stone-300"
      }`}
    >
      {children}
    </button>
  );
}

function Status({ stats }: { stats: Stats | null }) {
  const run = stats?.last_refresh;
  if (!stats || !run?.finished_at) return null;
  const stale = hoursSince(run.finished_at) > 26;
  const today = new Date(run.finished_at).toDateString() === new Date().toDateString();
  return (
    <p className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-stone-600">
      <span className="font-semibold tabular-nums text-stone-900">{stats.totals.visible.toLocaleString("en-IN")} open roles</span>
      <span className="text-stone-300" aria-hidden>
        /
      </span>
      <span className="inline-flex items-center gap-1.5" title="The list refreshes automatically every morning at 6:00 IST">
        <span className={`h-1.5 w-1.5 rounded-full ${stale ? "bg-amber-500" : "bg-emerald-500"}`} aria-hidden />
        {stale ? "Last refreshed" : "Refreshed"} {today ? "today" : new Date(run.finished_at).toLocaleDateString("en-IN", { day: "numeric", month: "short" })},{" "}
        {istTime(run.finished_at)} IST
      </span>
    </p>
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
  const [error, setError] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetchFacets().then(setFacets).catch(() => setFacets(null));
  }, []);

  // "/" focuses search, like most search-first sites
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement;
      if (e.key === "/" && !typing) {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
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
    setError(false);
    fetchJobs(f, p, ctrl.signal)
      .then((res) => {
        setItems((prev) => (p === 1 ? res.items : [...prev, ...res.items]));
        setTotal(res.total);
        setPage(p);
      })
      .catch((e) => {
        if (e.name !== "AbortError") setError(true);
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
  }

  const topCities = (facets?.city ?? []).filter((c) => !NON_CITIES.has(c.value)).slice(0, 6);
  // The selected city goes first so it's never scrolled out of view on a phone.
  const quickCities = filters.city
    ? [{ value: filters.city, count: 0 }, ...topCities.filter((c) => c.value !== filters.city)]
    : topCities;

  const active: { label: string; clear: () => void }[] = [
    filters.q && { label: `“${filters.q}”`, clear: () => update({ q: "" }) },
    filters.role && { label: filters.role, clear: () => update({ role: "" }) },
    filters.city && { label: filters.city, clear: () => update({ city: "" }) },
    filters.job_type && { label: JOB_TYPE_LABEL[filters.job_type], clear: () => update({ job_type: "" }) },
    filters.days && { label: POSTED[filters.days], clear: () => update({ days: "" }) },
  ].filter(Boolean) as { label: string; clear: () => void }[];

  const firstLoad = loading && page === 1 && items.length === 0;
  const refetching = loading && page === 1 && items.length > 0;

  return (
    <div>
      <section className="pb-2 pt-1">
        <h1 className="text-[26px] font-semibold leading-tight tracking-tight sm:text-[32px]">Jobs and internships for freshers in India</h1>
        <p className="mt-2 max-w-[60ch] text-[15px] leading-relaxed text-stone-600">
          Collected every morning from public job boards and company career pages, with duplicates removed and every post tagged by role and city.
        </p>
        <Status stats={stats} />
      </section>

      {/* Only the search box stays pinned; on a phone, pinned filters would eat half the screen. */}
      <div className="sticky top-0 z-20 -mx-4 bg-stone-50/90 px-4 pb-2 pt-3 backdrop-blur-md supports-[backdrop-filter]:bg-stone-50/75">
        <label className="relative block">
          <span className="sr-only">Search jobs</span>
          <SearchIcon className="pointer-events-none absolute left-3.5 top-1/2 h-[18px] w-[18px] -translate-y-1/2 text-stone-400" />
          <input
            ref={searchRef}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search a role, skill or company"
            enterKeyHint="search"
            className="h-12 w-full rounded-xl border border-stone-200 bg-white pl-10 pr-11 text-[15px] shadow-[0_1px_2px_rgb(28_25_23/0.04)] outline-none transition-[border-color,box-shadow] duration-150 placeholder:text-stone-400 hover:border-stone-300 focus:border-teal-600 focus:ring-4 focus:ring-teal-600/15 [&::-webkit-search-cancel-button]:hidden"
          />
          {query ? (
            <button
              type="button"
              onClick={() => {
                setQuery("");
                update({ q: "" });
                searchRef.current?.focus();
              }}
              className="absolute right-2 top-1/2 grid h-8 w-8 -translate-y-1/2 place-items-center rounded-lg text-stone-400 hover:bg-stone-100 hover:text-stone-700"
              aria-label="Clear search"
            >
              <XIcon className="h-4 w-4" />
            </button>
          ) : (
            <kbd className="pointer-events-none absolute right-3 top-1/2 hidden -translate-y-1/2 rounded border border-stone-200 px-1.5 text-[11px] text-stone-400 sm:block">/</kbd>
          )}
        </label>
      </div>

      <div className="space-y-3 pt-2">
        <div className="no-scrollbar -mx-4 flex gap-1.5 overflow-x-auto px-4" role="radiogroup" aria-label="Job type">
          {TYPES.map((t) => (
            <Chip key={t || "all"} role="radio" aria-checked={filters.job_type === t} active={filters.job_type === t} onClick={() => update({ job_type: t })}>
              {TYPE_CHIP[t]}
            </Chip>
          ))}
        </div>

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <div className="col-span-2 sm:col-span-1">
            <Select label="Role" value={filters.role} onChange={(role) => update({ role })}>
              <option value="">All roles</option>
              {facets?.role.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.value} ({r.count})
                </option>
              ))}
            </Select>
          </div>
          <Select label="City" value={filters.city} onChange={(city) => update({ city })}>
            <option value="">All cities</option>
            {facets?.city
              .filter((c) => c.value !== "Unspecified" && c.value !== "Other")
              .map((c) => (
                <option key={c.value} value={c.value}>
                  {c.value} ({c.count})
                </option>
              ))}
          </Select>
          <Select label="Posted" value={filters.days} onChange={(days) => update({ days })}>
            {Object.entries(POSTED).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </div>

        {quickCities.length > 0 && (
          <div className="no-scrollbar -mx-4 flex items-center gap-1.5 overflow-x-auto px-4">
            <span className="shrink-0 pr-1 text-xs text-stone-500">Popular:</span>
            {quickCities.map((c) => (
              <Chip key={c.value} active={filters.city === c.value} onClick={() => update({ city: filters.city === c.value ? "" : c.value })} aria-pressed={filters.city === c.value}>
                {c.value}
              </Chip>
            ))}
          </div>
        )}
      </div>

      <div className="mb-3 mt-5 flex min-h-8 flex-wrap items-center gap-x-3 gap-y-2">
        <p className="text-sm text-stone-600" aria-live="polite">
          {firstLoad ? (
            "Loading jobs…"
          ) : error ? null : (
            <>
              <span className="font-semibold tabular-nums text-stone-900">{total.toLocaleString("en-IN")}</span> {total === 1 ? "job" : "jobs"}
              {filters.q && !filters.sort && total > 0 && " · best match first"}
            </>
          )}
        </p>
        {active.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            {active.map((a) => (
              <button
                key={a.label}
                type="button"
                onClick={a.clear}
                className="inline-flex h-7 items-center gap-1 rounded-full bg-teal-50 pl-2.5 pr-1.5 text-xs font-medium text-teal-800 ring-1 ring-inset ring-teal-600/20 hover:bg-teal-100"
                aria-label={`Remove filter ${a.label}`}
              >
                {a.label}
                <XIcon className="h-3 w-3" />
              </button>
            ))}
            {active.length > 1 && (
              <button type="button" onClick={reset} className="text-xs font-medium text-stone-500 underline-offset-2 hover:text-stone-900 hover:underline">
                Clear all
              </button>
            )}
          </div>
        )}
        {filters.q && (
          <select
            value={filters.sort || "relevance"}
            onChange={(e) => update({ sort: e.target.value as Filters["sort"] })}
            className="ml-auto h-7 rounded-md bg-transparent text-xs text-stone-600 outline-none hover:text-stone-900"
            aria-label="Sort results"
          >
            <option value="relevance">Best match</option>
            <option value="newest">Newest first</option>
          </select>
        )}
      </div>

      {error && (
        <div className="flex items-start gap-3 rounded-xl border border-red-200 bg-red-50/60 p-4 text-sm text-red-800" role="alert">
          <AlertIcon className="mt-0.5 h-5 w-5 text-red-600" />
          <div className="flex-1">
            <p className="font-medium">Couldn't load jobs.</p>
            <p className="mt-0.5 text-red-700/90">The job list didn't respond. Check your connection and try again.</p>
          </div>
          <button
            type="button"
            onClick={() => load(filters, 1)}
            className="shrink-0 rounded-lg bg-white px-3 py-1.5 text-xs font-medium text-red-800 ring-1 ring-inset ring-red-200 hover:bg-red-50"
          >
            Retry
          </button>
        </div>
      )}

      {firstLoad && (
        <div className="space-y-3">
          {Array.from({ length: 4 }, (_, i) => (
            <JobCardSkeleton key={i} />
          ))}
        </div>
      )}

      {!firstLoad && !error && (
        <div className={`space-y-3 transition-opacity duration-150 ${refetching ? "opacity-50" : ""}`} aria-busy={refetching}>
          {items.map((job) => (
            <JobCard key={job.id} job={job} />
          ))}
        </div>
      )}

      {!loading && !error && items.length === 0 && (
        <div className="rounded-xl border border-dashed border-stone-300 bg-white/50 px-6 py-10 text-center">
          <p className="font-medium text-stone-900">No jobs match {active.length > 1 ? "these filters" : "this filter"}</p>
          <p className="mx-auto mt-1 max-w-[42ch] text-sm text-stone-600">
            {filters.q ? "Try a shorter or more general search, like “analyst” instead of a full job title, " : "Try "}
            removing a filter, or check back tomorrow: new posts arrive every morning.
          </p>
          {active.length > 0 && (
            <button type="button" onClick={reset} className="mt-4 rounded-lg bg-stone-900 px-4 py-2 text-sm font-medium text-white hover:bg-teal-700">
              Show all jobs
            </button>
          )}
        </div>
      )}

      {!error && items.length > 0 && items.length < total && (
        <button
          type="button"
          onClick={() => load(filters, page + 1)}
          disabled={loading}
          className="mt-4 h-11 w-full rounded-xl border border-stone-200 bg-white text-sm font-medium text-stone-700 transition-colors hover:border-stone-300 hover:text-stone-900 disabled:cursor-wait disabled:opacity-60"
        >
          {loading ? "Loading…" : `Show ${Math.min(20, total - items.length)} more of ${(total - items.length).toLocaleString("en-IN")}`}
        </button>
      )}
    </div>
  );
}
