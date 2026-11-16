// Small inline icon set, one stroke weight (1.6) on a 20px grid.
type P = { className?: string };
const base = "shrink-0";

export const SearchIcon = ({ className = "h-4 w-4" }: P) => (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" className={`${base} ${className}`} aria-hidden>
    <circle cx="9" cy="9" r="5.5" />
    <path d="m13.5 13.5 3 3" />
  </svg>
);

export const PinIcon = ({ className = "h-3.5 w-3.5" }: P) => (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" className={`${base} ${className}`} aria-hidden>
    <path d="M10 17.5s5.5-5 5.5-9.25a5.5 5.5 0 0 0-11 0C4.5 12.5 10 17.5 10 17.5Z" />
    <circle cx="10" cy="8.25" r="1.75" />
  </svg>
);

export const ExternalIcon = ({ className = "h-3.5 w-3.5" }: P) => (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className={`${base} ${className}`} aria-hidden>
    <path d="M8 5H5.5A1.5 1.5 0 0 0 4 6.5v8A1.5 1.5 0 0 0 5.5 16h8a1.5 1.5 0 0 0 1.5-1.5V12" />
    <path d="M11 4h5v5M16 4l-7 7" />
  </svg>
);

export const XIcon = ({ className = "h-3.5 w-3.5" }: P) => (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" className={`${base} ${className}`} aria-hidden>
    <path d="m6 6 8 8M14 6l-8 8" />
  </svg>
);

export const ChevronIcon = ({ className = "h-4 w-4" }: P) => (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className={`${base} ${className}`} aria-hidden>
    <path d="M6 8l4 4 4-4" />
  </svg>
);

export const GitHubIcon = ({ className = "h-4 w-4" }: P) => (
  <svg viewBox="0 0 16 16" fill="currentColor" className={`${base} ${className}`} aria-hidden>
    <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
  </svg>
);

export const AlertIcon = ({ className = "h-5 w-5" }: P) => (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" className={`${base} ${className}`} aria-hidden>
    <circle cx="10" cy="10" r="7.25" />
    <path d="M10 6.5v4M10 13.5v.01" />
  </svg>
);
