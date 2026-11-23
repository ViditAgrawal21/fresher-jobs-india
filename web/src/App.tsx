import { useEffect, useLayoutEffect, useState } from "react";
import { Analytics } from "@vercel/analytics/react";
import { Layout, restoreScroll } from "./components/Layout";
import { fetchStats, type Stats } from "./lib/api";
import { AboutPage } from "./pages/AboutPage";
import { JobPage } from "./pages/JobPage";
import { JobsPage } from "./pages/JobsPage";

// Three routes don't need a router library; the server sends index.html for any path.
function usePath() {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  return path;
}

export default function App() {
  const path = usePath();
  const [stats, setStats] = useState<Stats | null>(null);
  const jobId = path.match(/^\/jobs\/(\d+)\/?$/)?.[1];

  useEffect(() => {
    fetchStats().then(setStats).catch(() => setStats(null));
  }, []);

  // Back from a job detail page lands where you were in the list.
  useLayoutEffect(() => {
    if (path === "/") restoreScroll();
  }, [path]);

  return (
    <>
      <Layout path={path}>
        {/* The list stays mounted while a job is open, so going back keeps its results and "show more" pages. */}
        <div hidden={path !== "/"}>
          <JobsPage stats={stats} />
        </div>
        {jobId && <JobPage key={jobId} id={Number(jobId)} />}
        {path === "/about" && <AboutPage stats={stats} />}
        {path !== "/" && !jobId && path !== "/about" && (
          <div className="py-16 text-center">
            <p className="font-medium">Page not found</p>
            <a href="/" className="mt-2 inline-block text-sm text-teal-700 underline">
              Back to all jobs
            </a>
          </div>
        )}
      </Layout>
      <Analytics />
    </>
  );
}
