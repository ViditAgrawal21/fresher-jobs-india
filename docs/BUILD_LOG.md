# Build log: what broke and how it was fixed

Real issues hit while building, in order. Each one has the symptom, the cause and the fix.

---

### 1. Keyword filters can't tell a fresher job from a senior one (16 Nov)
**Symptom:** while probing company job boards, a title filter for entry-level words (`associate`, `analyst`, `intern`...) let through *Associate Director – Delivery*, *Senior Associate – Corporate Strategy* and *Assistant Manager – Internal Audit*. Of 907 India postings across 28 boards, the naive filter flagged 91 as entry-level, and a good share of those weren't.

**Cause:** "Associate" and "Analyst" mean anything from 0 to 10 years depending on the company.

**Fix:** two layers.
- A cheap rule filter drops titles that are clearly senior (`senior`, `manager`, `director`, `lead`...) before anything costs money. An explicit entry word wins: *Junior Architect* and *Lead Generation Intern* stay in.
- The LLM decides `is_fresher` from the description ("0–1 years", "freshers can apply"), and the site only shows `is_fresher = true`.

Also found: SmartRecruiters returns an `experienceLevel` field, so `mid_senior_level` / `director` / `executive` posts are skipped there before the detail call.

### 2. Fuzzy dedup merged "Product Analyst" with "Product Analyst II" (16 Nov)
**Symptom:** a unit test caught it. `token_sort_ratio("product analyst", "product analyst ii") = 90.9`, which is over the 90 threshold, so two different jobs collapsed into one.

**Cause:** for short titles, one extra token barely moves a character-based similarity score. (`token_set_ratio`, the usual choice, is worse here: it scores a subset as 100.)

**Fix:** level markers (`I`, `II`, `III`, `intern`, `trainee`, `junior`...) must match exactly before fuzzy matching is even tried. Raising the threshold would just move the problem to the next pair of titles.

### 3. Lever's API made a 1-minute job take 3+ minutes (16 Nov)
**Symptom:** the first dry run spent 179 s on Lever's 3 boards, against 50 s for Greenhouse's 14. Timing it directly with curl: Zeta took 59.5 s for ~1 MB, CRED dropped the connection after 80 s, and a retry of CRED took 77 s for 122 KB.

**Cause:** Lever's public API is slow, but it isn't payload size (paging with `limit=50` didn't help). Our 30 s read timeout plus 4 retries with backoff meant we abandoned slow-but-working requests and started them over.

**Fix:**
- Per-source read timeout, with Lever at 90 s, so a slow answer is waited for instead of retried.
- All sources now fetch **in parallel**. They are different hosts, and each keeps its own politeness delay, so run time is the slowest source instead of the sum.
- A board that fails is now recorded on the run (`partial` status) instead of only being logged.

### 4. First real run died: "server closed the connection unexpectedly" (16 Nov)
**Symptom:** run #1 fetched everything, then crashed on the very first DB write (`upsert_jobs`). The crash handler then failed too (`the connection is closed`), so run #1 stayed `status = 'running'` forever with 0 jobs saved.

**Cause:** `run.py` opened one DB connection at the start and held it while fetching, which takes several minutes because SmartRecruiters needs one detail call per new posting. Neon's free tier scales the compute to zero after ~5 idle minutes, which drops open connections. The error path then reused that same dead connection.

**Fix:**
- One short-lived connection per phase (start, write, finish). No connection is held during the slow parts, fetching and LLM calls.
- The failure path opens a **fresh** connection to record the failure.
- Each run first marks runs stuck in `running` for over 1 hour as `failed`, so a dead process can't leave a phantom "in progress" run on the stats page.

**Lesson:** with serverless Postgres, treat connections as cheap and short-lived, not as something you open once per process.

### 5. Dedup merged "Enterprise Account Executive" into "Marketing AI Engineer" (16 Nov)
**Symptom:** run #2 reported 80 duplicates among 450 posts. That seemed high for company boards, so I looked at the pairs. Most were nonsense: at Elastic and Stripe, unrelated jobs had been folded into a single "kept" post.

**Cause:** Elastic and Stripe host their Greenhouse jobs on their own careers page, e.g. `elastic.co/careers?gh_jid=123`. My URL cleaner treated `gh_jid` as a tracking parameter and stripped it, so every Elastic job had the same canonical URL. "Same URL ⇒ same job" then merged them all.

**Fix:** only strip real tracking params (`utm_*`, `gh_src`, `ref`, `fbclid`...). Kept `gh_jid`, with a regression test that two different `gh_jid` URLs stay different.

**Lesson:** sanity-check the counters against the actual rows. The dedup unit tests passed; it was the real data that broke the assumption.

### 6. The expiry step hid 162 live company postings (16 Nov)
**Symptom:** the same run "expired" 162 posts it had just fetched.

**Cause:** the rule "hide posts older than 45 days" was written with aggregators in mind. Companies keep roles open for months on their own boards (one Greenhouse post was first published in 2020). If a company's board still lists the job, it's open.

**Fix:** the age limit applies only to aggregators (Adzuna, Jooble), which keep listing filled jobs. Company-board posts expire only when they disappear from the board for 7 days.
