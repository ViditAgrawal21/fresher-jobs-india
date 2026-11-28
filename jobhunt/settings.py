"""Settings for the personal job-hunt toolkit. Read from .env / .env.local like the rest of the repo."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent


class JobhuntSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")

    # Contact details stay out of git (the repo is public); they're printed on every resume.
    candidate_phone: str = ""
    candidate_email: str = ""

    # Resume tailoring. Without a key (or with --offline) a keyword-based tailoring is used.
    anthropic_api_key: str = ""
    tailor_model: str = "claude-opus-5-5"
    tailor_effort: str = "medium"

    # Gmail sync over IMAP. Needs 2-step verification and an App Password
    # (Google Account -> Security -> App passwords), never your normal password.
    gmail_address: str = ""
    gmail_app_password: str = ""
    imap_host: str = "imap.gmail.com"

    # Where the tracker, tailored resumes and sync state live. Git-ignored by default.
    jobhunt_data_dir: str = ""
    jobhunt_profile: str = ""
    # Optional: a Chrome/Chromium binary for PDF rendering if `playwright install chromium` isn't used.
    chromium_path: str = ""

    followup_after_days: int = 7
    # Gmail sync reads mail from the day you started applying (your first application in the
    # tracker), not your whole mailbox. Set MAIL_SINCE=YYYY-MM-DD to choose the start yourself.
    mail_since: str = ""

    # Freshness caps, in days since the company first published a job. `discover` keeps jobs up to
    # FRESH_JOB_DAYS old; jobs older than STALE_JOB_DAYS are marked old and skipped by autofill.
    fresh_job_days: int = 30
    stale_job_days: int = 60

    # Where you want to work, in order: remote in India, remote roles open to India (if
    # REMOTE_ABROAD), then hybrid and office jobs in PREFERRED_CITIES. Other places are left out.
    preferred_cities: str = "Pune, Mumbai, Bengaluru"
    remote_abroad: bool = True

    # Autopilot (dashboard): how often it checks email, checks jobs are still open and searches for
    # new jobs (0 turns one off), and how many new matches a day it tailors resumes for.
    autopilot_mail_hours: float = 2
    autopilot_verify_hours: float = 12
    autopilot_discover_hours: float = 24
    autopilot_followup_hours: float = 24   # draft follow-ups in Gmail for applications with no reply
    auto_tailor_per_day: int = 5
    auto_tailor_min_score: int = 70

    @property
    def gmail_login(self) -> tuple[str, str]:
        """(address, app password): the address defaults to CANDIDATE_EMAIL; spaces in the password are ignored."""
        return (self.gmail_address or self.candidate_email), self.gmail_app_password.replace(" ", "")

    @property
    def data_dir(self) -> Path:
        return Path(self.jobhunt_data_dir) if self.jobhunt_data_dir else PACKAGE_DIR / "data"

    @property
    def tracker_path(self) -> Path:
        return self.data_dir / "tracker.xlsx"

    @property
    def profile_path(self) -> Path:
        return Path(self.jobhunt_profile) if self.jobhunt_profile else PACKAGE_DIR / "profile.yaml"


@lru_cache
def get_settings() -> JobhuntSettings:
    return JobhuntSettings()
