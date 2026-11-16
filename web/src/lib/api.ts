export type Job = {
  id: number;
  title: string;
  company: string;
  url: string;
  location_raw: string;
  source: string;
  role: string;
  cities: string[];
  other_city: string | null;
  work_mode: "onsite" | "hybrid" | "remote" | "unknown";
  job_type: "internship" | "full_time" | "contract" | "apprenticeship";
  posted_at: string;
  also_on: string[];
};

export type JobsResponse = { total: number; page: number; page_size: number; items: Job[] };

export type JobDetail = Omit<Job, "also_on"> & {
  description: string;
  first_seen_at: string;
  last_seen_at: string;
  listed: boolean;
  also_on: { source: string; url: string }[];
  similar: Job[];
};

export type FacetValue = { value: string; count: number };
export type Facets = Record<"role" | "city" | "job_type" | "source", FacetValue[]>;

export type Run = {
  id: number;
  started_at: string;
  finished_at: string | null;
  status: "running" | "ok" | "partial" | "failed";
  stats: Record<string, number | boolean>;
  errors?: { source: string; error: string }[];
};

export type EvalSummary = {
  n: number;
  model: string;
  prompt_version: string;
  role_accuracy: number;
  city_accuracy: number;
  both_accuracy: number;
  fresher_precision: number | null;
  role_ci: [number, number];
  city_ci: [number, number];
  labels?: { source: string; reviewed: number; corrected: number };
  compare?: { model: string; prompt_version: string; role_accuracy: number; city_accuracy: number; both_accuracy: number; cost_per_1000_usd: number }[];
};

export type Stats = {
  totals: { visible: number; active: number; duplicates: number; not_fresher: number; outside_india: number; untagged: number; all_time: number };
  last_refresh: Run | null;
  runs: Run[];
  cost: { total_usd: number; posts_tagged: number; per_1000_posts_usd: number | null };
  eval: EvalSummary | null;
};

export type Filters = {
  q: string;
  role: string;
  city: string;
  job_type: string;
  days: string;
  sort: "" | "relevance" | "newest";
};

export const EMPTY_FILTERS: Filters = { q: "", role: "", city: "", job_type: "", days: "", sort: "" };

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { signal });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

export function fetchJobs(f: Filters, page: number, signal?: AbortSignal) {
  const p = new URLSearchParams({ page: String(page), page_size: "20" });
  if (f.q.trim()) p.set("q", f.q.trim());
  if (f.role) p.set("role", f.role);
  if (f.city) p.set("city", f.city);
  if (f.job_type) p.set("job_type", f.job_type);
  if (f.days) p.set("days", f.days);
  if (f.sort) p.set("sort", f.sort);
  return get<JobsResponse>(`/api/jobs?${p}`, signal);
}

export class NotFoundError extends Error {}

export async function fetchJob(id: number, signal?: AbortSignal): Promise<JobDetail> {
  const res = await fetch(`/api/jobs/${id}`, { signal });
  if (res.status === 404) throw new NotFoundError("job not found");
  if (!res.ok) throw new Error(`/api/jobs/${id} -> ${res.status}`);
  return res.json() as Promise<JobDetail>;
}

export const fetchFacets = () => get<Facets>("/api/facets");
export const fetchStats = () => get<Stats>("/api/stats");
