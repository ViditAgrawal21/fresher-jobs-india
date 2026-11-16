import { useEffect, useState } from "react";
import { Layout } from "./components/Layout";
import { fetchStats, type Stats } from "./lib/api";
import { AboutPage } from "./pages/AboutPage";
import { JobsPage } from "./pages/JobsPage";

// Two pages don't need a router; the server sends index.html for any path.
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

  useEffect(() => {
    fetchStats().then(setStats).catch(() => setStats(null));
  }, []);

  return (
    <Layout path={path}>
      {path === "/about" ? <AboutPage stats={stats} /> : <JobsPage stats={stats} />}
    </Layout>
  );
}
