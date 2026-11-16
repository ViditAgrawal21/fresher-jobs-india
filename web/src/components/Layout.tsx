import type { ReactNode } from "react";
import { GitHubIcon } from "./Icons";

export const REPO_URL = "https://github.com/ViditAgrawal21/fresher-jobs-india";

const scrollPositions = new Map<string, number>();
const here = () => window.location.pathname + window.location.search;

export function navigate(path: string) {
  scrollPositions.set(here(), window.scrollY);
  window.history.pushState(null, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
  window.scrollTo(0, 0);
}

/** Call after the page for the current URL has rendered (on back/forward). */
export function restoreScroll() {
  window.scrollTo(0, scrollPositions.get(here()) ?? 0);
}

export function goBackOr(fallback: string) {
  // came from inside the site -> real back (keeps list state); deep link -> go to the list
  if (scrollPositions.size > 0) window.history.back();
  else navigate(fallback);
}

function NavLink({ to, children, active }: { to: string; children: ReactNode; active: boolean }) {
  return (
    <a
      href={to}
      aria-current={active ? "page" : undefined}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey) return;
        e.preventDefault();
        navigate(to);
      }}
      className={`relative rounded-md px-2.5 py-1.5 text-sm transition-colors ${
        active ? "font-medium text-stone-900" : "text-stone-500 hover:text-stone-900"
      }`}
    >
      {children}
      {active && <span className="absolute inset-x-2.5 -bottom-[15px] h-0.5 rounded-full bg-teal-600" aria-hidden />}
    </a>
  );
}

export function Layout({ path, children }: { path: string; children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col bg-stone-50 text-stone-900">
      <header className="border-b border-stone-200 bg-white">
        <div className="mx-auto flex h-14 max-w-3xl items-center justify-between gap-3 px-4">
          <a
            href="/"
            onClick={(e) => {
              e.preventDefault();
              navigate("/");
            }}
            className="flex items-center gap-2 font-semibold tracking-tight"
          >
            <img src="/favicon.svg" alt="" className="h-6 w-6" />
            <span>Fresher Jobs India</span>
          </a>
          <nav className="flex items-center gap-0.5">
            <NavLink to="/" active={path === "/" || path.startsWith("/jobs/")}>Jobs</NavLink>
            <NavLink to="/about" active={path === "/about"}>How it works</NavLink>
            <a
              href={REPO_URL}
              target="_blank"
              rel="noopener"
              className="ml-1 hidden rounded-md p-2 text-stone-400 transition-colors hover:text-stone-900 sm:block"
              aria-label="Source code on GitHub"
            >
              <GitHubIcon className="h-[18px] w-[18px]" />
            </a>
          </nav>
        </div>
      </header>

      <main className="mx-auto w-full max-w-3xl flex-1 px-4 pb-16 pt-6 sm:pt-8">{children}</main>

      <footer className="border-t border-stone-200 bg-white">
        <div className="mx-auto flex max-w-3xl flex-col gap-1 px-4 py-6 text-xs leading-relaxed text-stone-500 sm:flex-row sm:justify-between">
          <p>
            Listings link to the original post.{" "}
            <a href="https://www.adzuna.in" target="_blank" rel="noopener" className="underline underline-offset-2 hover:text-stone-900">
              Jobs by Adzuna
            </a>{" "}
            and company career pages.
          </p>
          <p>
            Built by Vidit Agrawal ·{" "}
            <a href={REPO_URL} target="_blank" rel="noopener" className="underline underline-offset-2 hover:text-stone-900">
              Source on GitHub
            </a>
          </p>
        </div>
      </footer>
    </div>
  );
}
