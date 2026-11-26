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
