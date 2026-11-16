from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from core.taxonomy import CITY_VALUES, ROLE_VALUES

# Literal[...] built from the taxonomy so the JSON schema sent to the LLM has real enums.
Role = Literal[tuple(ROLE_VALUES)]  # type: ignore[valid-type]
City = Literal[tuple(CITY_VALUES)]  # type: ignore[valid-type]


class RawPost(BaseModel):
    """What every source returns. Kept close to the source's own fields."""

    source: str
    source_id: str
    url: str
    title: str
    company: str
    location: str = ""
    # None means "we already have this post and skipped re-fetching its details".
    description: str | None = ""
    posted_at: datetime | None = None


class JobTags(BaseModel):
    """LLM output for one post."""

    id: int
    role: Role
    cities: list[City] = Field(description="Every city the job can be done from. Use Remote / Pan India when that is what the post says.")
    other_city: str | None = Field(default=None, description="Only when cities contains Other: the city name as written.")
    work_mode: Literal["onsite", "hybrid", "remote", "unknown"]
    job_type: Literal["internship", "full_time", "contract", "apprenticeship"]
    is_fresher: bool = Field(description="True if a fresh graduate / student with 0-1 years of experience can apply.")
    confidence: Literal["high", "medium", "low"]


class TagBatch(BaseModel):
    results: list[JobTags]
