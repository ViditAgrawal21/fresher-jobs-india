import { useCallback, useEffect, useState } from "react";
import { EMPTY_FILTERS, type Filters } from "./api";

const KEYS = Object.keys(EMPTY_FILTERS) as (keyof Filters)[];

function read(): Filters {
  const p = new URLSearchParams(window.location.search);
  const f = { ...EMPTY_FILTERS };
  for (const k of KEYS) {
    const v = p.get(k);
    if (v) (f as Record<string, string>)[k] = v;
  }
  return f;
}

/** Filters live in the URL so a search can be shared or bookmarked. */
export function useUrlFilters(): [Filters, (patch: Partial<Filters>) => void, () => void] {
  const [filters, setFilters] = useState<Filters>(read);

  useEffect(() => {
    const onPop = () => setFilters(read());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const write = useCallback((next: Filters) => {
    const p = new URLSearchParams();
    for (const k of KEYS) if (next[k]) p.set(k, next[k]);
    const qs = p.toString();
    window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
    setFilters(next);
  }, []);

  const update = useCallback((patch: Partial<Filters>) => write({ ...read(), ...patch }), [write]);
  const reset = useCallback(() => write({ ...EMPTY_FILTERS }), [write]);
  return [filters, update, reset];
}
