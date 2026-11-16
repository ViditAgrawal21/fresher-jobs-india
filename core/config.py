from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # .env.local is written by `neon init` (DB URLs); .env holds the API keys. Later file wins.
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")

    database_url: str = ""
    # Neon gives a pooled and a direct URL. Schema changes go through the direct one.
    database_url_unpooled: str = ""

    anthropic_api_key: str = ""
    tagger_model: str = "claude-haiku-5-5"
    tagger_batch_size: int = 20
    tagger_workers: int = 4

    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    adzuna_pages_per_query: int = 2
    jooble_api_key: str = ""

    # Jobs we haven't seen in this many days (from a source that ran fine) are hidden.
    stale_after_days: int = 7
    max_post_age_days: int = 45

    user_agent: str = "FresherJobsIndia/1.0 (+https://beep.viditagrawal.in/about)"
    log_level: str = "INFO"

    @property
    def migrations_url(self) -> str:
        return self.database_url_unpooled or self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
