import type { ReactNode } from "react";

const REPO_URL = "https://github.com/ViditAgrawal21/fresher-jobs-india";

export function navigate(path: string) {
  window.history.pushState(null, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
  window.scrollTo(0, 0);
}

function NavLink({ to, children, active }: { to: string; children: ReactNode; active: boolean }) {
  return (
    <a
      href={to}
      onClick={(e) => {
        e.preventDefault();
        navigate(to);
      }}
      className={`rounded-md px-2.5 py-1.5 text-sm ${active ? "font-medium text-stone-900" : "text-stone-500 hover:text-stone-900"}`}
    >
      {children}
    </a>
  );
}

export function Layout({ path, children }: { path: string; children: ReactNode }) {
  return (
    <div className="min-h-dvh bg-stone-50 text-stone-900">
      <header className="border-b border-stone-200 bg-white">
        <div className="mx-auto flex h-14 max-w-3xl items-center justify-between px-4">
          <a
            href="/"
            onClick={(e) => {
              e.preventDefault();
              navigate("/");
            }}
            className="flex items-center gap-2 font-semibold tracking-tight"
          >
            <img src="/favicon.svg" alt="" className="h-6 w-6" />
            Fresher Jobs India
          </a>
          <nav className="flex gap-1">
            <NavLink to="/" active={path === "/"}>Jobs</NavLink>
            <NavLink to="/about" active={path === "/about"}>How it works</NavLink>
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 pb-16 pt-6">{children}</main>

      <footer className="border-t border-stone-200 bg-white">
        <div className="mx-auto max-w-3xl space-y-1 px-4 py-6 text-xs text-stone-500">
          <p>
            Listings come from public job APIs and company career boards and link to the original post.{" "}
            <a href="https://www.adzuna.in" target="_blank" rel="noopener" className="underline">Jobs by Adzuna</a>.
          </p>
          <p>
            Built by Vidit Agrawal ·{" "}
            <a href={REPO_URL} target="_blank" rel="noopener" className="underline">Source on GitHub</a>
          </p>
        </div>
      </footer>
    </div>
  );
}
