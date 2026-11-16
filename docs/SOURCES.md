# Sources and why they're allowed

The task says "respect each site's terms", so the rule was simple: **only use data a site publishes for other people to consume**. That means official APIs and companies' own public job-board APIs. Nothing behind a login, nothing a site's terms forbid.

| Source | How | Terms | Notes |
|---|---|---|---|
| **Adzuna India** | Official REST API, free key | Publishing listings is allowed if each one credits Adzuna and links back | Main volume. Cards say "Jobs by Adzuna" and link through Adzuna's `redirect_url`. |
| **Jooble** | Official REST API, key on request | API provided for this | Optional; switched on when the key arrives. |
| **Greenhouse** | `boards-api.greenhouse.io/v1/boards/{company}/jobs` | Public Job Board API, no auth, made for publishing jobs | 14 companies |
| **Lever** | `api.lever.co/v0/postings/{company}` | Public Postings API, no auth | 3 companies. Slow API, so it gets a 90 s timeout. |
| **Ashby** | `api.ashbyhq.com/posting-api/job-board/{company}` | Public Posting API, no auth | 4 companies |
| **SmartRecruiters** | `api.smartrecruiters.com/v1/companies/{company}/postings` | Public Posting API, no auth | 6 companies. A detail call is made only for postings we haven't stored yet. |

**Not used, on purpose:** LinkedIn, Naukri, Indeed, Internshala, Glassdoor, foundit, Unstop. Their terms prohibit automated collection.

## How the company list was picked
On 16 Nov I probed a few hundred likely company slugs across the four platforms (Indian startups, and global companies with India offices), then kept the boards that had India postings with at least some entry-level titles. The list is in [pipeline/sources/boards.py](../pipeline/sources/boards.py); adding a company is one line.

## Being polite
- Identifying User-Agent with a link to the site.
- A delay between requests to the same host (0.5–1 s), and backoff on 429/5xx.
- Incremental fetching: SmartRecruiters detail pages are only fetched for new postings.
- We store only what's needed to show and search a post (description trimmed to 1,500 chars), and every card links to the original.
