# jobhunt: tailored resumes, an Excel tracker and Gmail sync

A personal job-search toolkit that lives next to the Fresher Jobs India pipeline and reuses its job sources.

1. **Discover** jobs on company boards (Greenhouse, Lever, Ashby, SmartRecruiters, plus Adzuna) that fit your stack and have at most 4 years of experience required. Only jobs published in the last 30 days are kept (`FRESH_JOB_DAYS`). Listings from recruiters, staffing firms or unnamed companies, or with scam signs (registration fees, "WhatsApp your CV"), are dropped.
   **Verify** that each job is real and still open by asking the company's own job board, which the careers page itself reads from. This runs on its own once a day when the dashboard starts, and again right before autofill. Closed jobs move to Withdrawn. Jobs published over 60 days ago (`STALE_JOB_DAYS`) are marked old, hidden by default and skipped by autofill. Jobs with a deadline go to the top.
2. **Apply**: tailor a one-page resume to each posting with Claude, under a guard that blocks invented facts, render it to PDF, check the ATS text layer, and add a tracker row.
3. **Track** everything in one Excel workbook: status, the email you applied with, replies, follow-ups, and weekly targets against actual.
4. **Sync Gmail** to mark which companies replied and move statuses forward (acknowledged, assessment, interview, offer, rejected). Confirmation emails for jobs you applied to elsewhere ("your application was sent to X" from LinkedIn, Naukri, Aperture or an ATS) add a tracker row automatically, so every application is counted.
5. **Dashboard** (`python -m jobhunt serve`): a local web page with the funnel, applications per week against your targets, the skills postings ask for (which you have, which are gaps), response rate by match % and by source, and every application with its keyword match and links to its tailored resume, report and job description. Moving a card on the pipeline board saves to `tracker.xlsx`.
6. **Autofill**: the Autofill button on a Ready card (or "Autofill the top N ready jobs") opens a Chrome window with one tab per application. Each form gets your details from `jobhunt/data/answers.yaml` and that job's tailored resume. Filled fields are outlined green; anything it couldn't answer is outlined orange for you.

With **Submit for me** ticked (next to Start autofill), it submits each form itself once every required question has an answer. It clicks Next through multi-page forms (Oracle, SmartRecruiters) and enters the security code some sites email you, read from your Gmail. Every question a form asks that `answers.yaml` doesn't cover is saved to `jobhunt/data/questions.yaml` with its choices. Answer each one once and every later form that asks it is filled and submitted. A form waits for you, with the reason shown on the dashboard, when it has a CAPTCHA, needs you to sign in or create an account (Workday: do it once in that window), or has a required question you haven't answered yet. Without Submit for me, you read every field and click Submit yourself.

## Autopilot, reports and email that learns

- **Autopilot** (switch on the dashboard, on by default) keeps things moving while `serve` runs:
  - checks Gmail for replies every 2 hours;
  - checks jobs are still open every 12 hours;
  - searches for new jobs once a day;
  - tailors resumes for up to 5 of the best new matches a day: JD match 70%+, fresh, in your places.

  Applying stays your click (Auto-apply). Change the timings in `.env` with `AUTOPILOT_MAIL_HOURS`, `AUTOPILOT_VERIFY_HOURS`, `AUTOPILOT_DISCOVER_HOURS`, `AUTO_TAILOR_PER_DAY` and `AUTO_TAILOR_MIN_SCORE`; 0 turns a part off.
- **Gmail sync** reads only job-related mail, starting the day you began applying (or `MAIL_SINCE`). Job alerts, promotions and social mail are filtered out on Gmail's side.
- **Emails to check:** when a recruiter writes from an address that doesn't name a company you track, the email is listed on the dashboard. Link it to its job once and that sender's domain is remembered. Shared domains (Gmail, Greenhouse, LinkedIn...) are never remembered.
- **Download Excel report** (or `python -m jobhunt report`): summary, pipeline and funnel, weekly progress, skills demand, response rates, every application, job matches, email log and questions, with charts. The counts are live formulas.
- **Interview prep sheets:** when a job reaches Assessment or Interviewing (you move it, or an email does), a one-page `interview_prep.md` is written next to its resume. It covers what they want, your best stories, likely technical and behavioural questions with hints from your profile, topics to revise (gaps first) and questions to ask them. Claude writes it when `ANTHROPIC_API_KEY` is set (about a minute, a few cents); otherwise it comes from a built-in question bank. Open it from **Prep sheet** on the card, or run `python -m jobhunt prep A012`.
- **Follow-up drafts:** **Draft follow-ups** on the dashboard (and Autopilot, once a day) writes a short check-in email into your Gmail **Drafts** for each application with no reply after 7 days. It goes to the recruiter when the tracker knows their address, never a no-reply one. Nothing is sent: you read and send it. At most one per application a week. `python -m jobhunt followups --drafts` does the same.
- `python -m jobhunt questions` shows where `answers.yaml` and `questions.yaml` are and which questions still need you, then opens both files.

## One-time setup (Windows, PowerShell)

```powershell
cd fresher-jobs-india
python -m venv .venv; .venv\Scripts\activate
pip install -r requirements-jobhunt.txt
playwright install chromium
copy .env.example .env      # then fill in the jobhunt section (and ANTHROPIC_API_KEY)
python -m jobhunt init      # creates jobhunt\data\tracker.xlsx
python -m jobhunt resume    # base resume per track -> jobhunt\data\base\
```

Then, every day, one command:

```powershell
python -m jobhunt serve     # http://127.0.0.1:8765 opens in your browser; Ctrl+C stops it
```

Or double-click `start-jobhunt.bat` in the repo folder. The first time, it creates `.venv` and installs the packages and Chromium; after that it just starts the dashboard.

The first time you autofill, check `jobhunt\data\answers.yaml`, which is created then. It holds your notice period, CTC, expected CTC, education dates and similar answers. Blank answers are left for you in each form. The autofill browser keeps its own profile in `jobhunt\data\browser-profile`. Sign in to an ATS there once and it stays signed in.

`jobhunt/data/` is git-ignored because this repo is public. Back it up to Google Drive or OneDrive, for example by setting `JOBHUNT_DATA_DIR` to a synced folder.

**Gmail App Password:** turn on 2-Step Verification, then go to Google Account → Security → App passwords and create one named "jobhunt". Put it in `GMAIL_APP_PASSWORD`. Also make sure IMAP is enabled in Gmail settings (Forwarding and POP/IMAP). The toolkit only reads mail, and only on your machine.

## Daily loop (about 60–90 minutes for 4–5 applications)

```powershell
python -m jobhunt sync-mail                 # replies since yesterday -> tracker (+ rows for untracked applications)
python -m jobhunt followups                 # who to nudge today
python -m jobhunt discover                  # new matching jobs -> Leads sheet (2-3x a week is enough)
python -m jobhunt serve                     # dashboard: board, autofill, resumes
```

For each job you want (from Leads, LinkedIn, Naukri or a referral):

```powershell
# from the Leads sheet (row number in Excel):
python -m jobhunt apply --lead 7

# from a posting you copied into a text file:
python -m jobhunt apply --jd jd.txt --company "Razorpay" --role "SDE 2 Backend" --url "<link>" --source LinkedIn

# after you submit it:
python -m jobhunt applied A012
```

Each application gets a folder `jobhunt/data/applications/A012_<company>_<role>/` containing:

- `Vidit_Agrawal_Resume.pdf`: the tailored one-page resume
- `report.md`: the fit score, matched skills and gaps, a recruiter message to paste, and every reworded line next to its original
- `job_description.txt` and `tailoring.json`, kept for interview prep later

Interview moved forward? Record it with `python -m jobhunt status A012 Interviewing --next "Round 2: system design" --next-date 2026-12-18`.

## How tailoring stays honest

`profile.yaml` holds every fact a resume may use. Claude (`TAILOR_MODEL`, default `claude-opus-5-5`) picks the track, orders skills, and picks and rewords bullets. A deterministic guard (`tailor.py: apply_tailoring`) then checks each line and reverts it to your original wording if it:

- names a technology that the original bullet or that job's stack doesn't,
- adds a number that isn't in your profile,
- or drifts too far from the original.

It also keeps every job on the page, so there are no unexplained gaps. Read `report.md` before you send anyway: you are the last check.

Without an API key, or with `--offline`, tailoring is keyword-based and free. It reorders skills and bullets but doesn't reword them. With Claude it costs roughly $0.05–0.10 per application on Opus 5.5, with the profile cached after the first call. Set `TAILOR_MODEL=claude-sonnet-5-5` to roughly halve that.

**Adding a fact** (a metric, a new project, RAG work) means adding a bullet to `profile.yaml`. Every later resume can then use it.

## Commands

| Command | What it does |
| --- | --- |
| `init` | Create the tracker workbook |
| `resume [--track backend\|fullstack\|ai\|mobile]` | Untailored resume per track |
| `apply --jd FILE \| --jd-url URL \| --lead ROW` | Tailor, render, and add a tracker row (status Tailored; `--applied` marks it Applied) |
| `applied ID` | Mark as submitted today (`--date`, `--with email`) |
| `status ID STATUS` | Applied, Assessment, Interviewing, Offer, Rejected, Ghosted, ... (`--next`, `--next-date`, `--note`) |
| `list [--status S]` | Applications in the terminal |
| `followups` | Applied 7+ days ago with no reply, plus a draft message |
| `sync-mail [--since DATE] [--dry-run]` | Read Gmail, update replies and statuses, log every match |
| `discover [--max-years 4] [--min-score 35] [--max-age-days 30]` | Fetch and score fresh openings from employers into the Leads sheet |
| `serve [--port 8765]` | The dashboard on localhost. Moves save to the tracker, links open each resume and report, and Autofill buttons work |
| `autofill IDS \| --all-ready [--limit 5] [--submit]` | Autofill from the terminal: open and fill those forms, then you check and submit each one |
| `verify [IDS]` | Ask each company's job board whether Ready jobs are still open and when they were published; closed ones move to Withdrawn |
| `dashboard [--open]` | Build a static `jobhunt/data/dashboard.html` (read-only) from the tracker and application folders |

`discover` checks the boards in `pipeline/sources/boards.py` plus the extra experienced-hire boards in `jobhunt/discover.py` (Paytm, MongoDB, Databricks, Hevo Data, InMobi, Mindtickle, Twilio, FamPay, Truecaller, Airbnb). To add a company, put its board slug in `EXTRA_GREENHOUSE`, `EXTRA_LEVER` or `EXTRA_ASHBY`. The URL shows it, for example `boards.greenhouse.io/<slug>` or `jobs.lever.co/<slug>`.
