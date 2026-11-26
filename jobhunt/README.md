# jobhunt: tailored resumes, an Excel tracker and Gmail sync

A personal job-search toolkit that lives next to the Fresher Jobs India pipeline and reuses its job sources.

1. **Discover** jobs on company boards (Greenhouse, Lever, Ashby, SmartRecruiters, plus Adzuna) that fit your stack and have at most 4 years of experience required.
2. **Apply**: tailor a one-page resume to each posting with Claude, under a guard that blocks invented facts, render it to PDF, check the ATS text layer, and add a tracker row.
3. **Track** everything in one Excel workbook: status, the email you applied with, replies, follow-ups, and weekly targets against actual.
4. **Sync Gmail** to mark which companies replied and move statuses forward (acknowledged, assessment, interview, offer, rejected).

You still click Submit yourself. Portals such as LinkedIn and Naukri forbid bots, and a person reading each posting for 30 seconds catches what no script does.

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

`jobhunt/data/` is git-ignored because this repo is public. Back it up to Google Drive or OneDrive, for example by setting `JOBHUNT_DATA_DIR` to a synced folder.

**Gmail App Password:** turn on 2-Step Verification, then go to Google Account → Security → App passwords and create one named "jobhunt". Put it in `GMAIL_APP_PASSWORD`. Also make sure IMAP is enabled in Gmail settings (Forwarding and POP/IMAP). The toolkit only reads mail, and only on your machine.

## Daily loop (about 60–90 minutes for 4–5 applications)

```powershell
python -m jobhunt sync-mail                 # replies since yesterday -> tracker
python -m jobhunt followups                 # who to nudge today
python -m jobhunt discover                  # new matching jobs -> Leads sheet (2-3x a week is enough)
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
| `discover [--max-years 4] [--min-score 35]` | Fetch and score openings into the Leads sheet |

To add companies to `discover`, put their board slug in `pipeline/sources/boards.py`. The URL shows it, for example `boards.greenhouse.io/<slug>` or `jobs.lever.co/<slug>`.
