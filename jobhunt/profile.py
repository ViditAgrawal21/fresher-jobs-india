"""The master profile (profile.yaml) and the resume document built from it."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from jobhunt.keywords import extract_terms, numbers_in

Track = Literal["backend", "fullstack", "ai", "mobile"]
TRACKS: tuple[str, ...] = ("backend", "fullstack", "ai", "mobile")


# ---------- profile.yaml ----------

class Link(BaseModel):
    label: str
    url: str

    @property
    def display(self) -> str:
        """What gets printed: the URL without scheme, so ATS parsers and printouts keep it."""
        return self.url.split("://", 1)[-1].removeprefix("www.").rstrip("/")


class SkillGroup(BaseModel):
    category: str
    items: list[str]


class Bullet(BaseModel):
    id: str
    text: str
    tags: list[str] = Field(default_factory=list)


class Job(BaseModel):
    id: str
    company: str
    title: str
    location: str = ""
    start: str
    end: str
    stack: list[str] = Field(default_factory=list)
    bullets: list[Bullet]


class Project(BaseModel):
    id: str
    name: str
    url: str = ""
    stack: list[str] = Field(default_factory=list)
    stack_line: str = ""
    bullets: list[Bullet]


class Education(BaseModel):
    school: str
    degree: str
    date: str
    grade: str = ""


class TrackSpec(BaseModel):
    label: str
    headline: str
    summary: str
    skill_order: list[str]
    projects: list[str]
    title_pattern: str = ""


class Profile(BaseModel):
    name: str
    years_of_experience: str
    links: list[Link]
    skills: list[SkillGroup]
    experience: list[Job]
    projects: list[Project]
    education: list[Education]
    achievements: list[str] = Field(default_factory=list)
    tracks: dict[str, TrackSpec]

    # --- lookups ---
    def job(self, job_id: str) -> Job | None:
        return next((j for j in self.experience if j.id == job_id), None)

    def project(self, project_id: str) -> Project | None:
        return next((p for p in self.projects if p.id == project_id), None)

    def all_skills(self) -> list[str]:
        return [s for g in self.skills for s in g.items]

    def full_text(self) -> str:
        parts = [s for g in self.skills for s in g.items]
        for j in self.experience:
            parts += [j.title, *j.stack, *(b.text for b in j.bullets)]
        for p in self.projects:
            parts += [p.name, p.stack_line, *p.stack, *(b.text for b in p.bullets)]
        return "\n".join(parts)

    def supported_terms(self) -> set[str]:
        """Every tech term the profile can back up."""
        return extract_terms(self.full_text())

    def known_numbers(self) -> set[str]:
        return numbers_in(self.full_text()) | numbers_in(self.years_of_experience) | numbers_in(" ".join(self.achievements))

    def track_summary(self, track: str) -> str:
        return self.tracks[track].summary.format(experience=self.years_of_experience)


def load_profile(path: Path) -> Profile:
    return Profile.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


# ---------- the rendered resume ----------

class ResumeEntry(BaseModel):
    """A job or a project as it appears on one resume."""

    id: str
    heading: str            # company or project name
    subheading: str = ""    # job title, or the project's stack line
    right: str = ""         # dates, or blank for projects
    right_sub: str = ""     # location
    url: str = ""
    bullets: list[str]
    bullet_ids: list[str] = Field(default_factory=list)


class ResumeDoc(BaseModel):
    name: str
    headline: str
    contact: list[str]
    links: list[Link]
    summary: str = ""
    skills: list[SkillGroup]
    experience: list[ResumeEntry]
    projects: list[ResumeEntry]
    education: list[Education]
    achievements: list[str]
    density: Literal["normal", "compact"] = "normal"

    def plain_text(self) -> str:
        lines = [self.name, self.headline, self.summary]
        for g in self.skills:
            lines.append(f"{g.category}: {', '.join(g.items)}")
        for e in self.experience + self.projects:
            lines += [e.heading, e.subheading, *e.bullets]
        return "\n".join(lines)


def job_entry(job: Job, bullet_ids: list[str] | None = None, texts: dict[str, str] | None = None) -> ResumeEntry:
    ids = bullet_ids or [b.id for b in job.bullets]
    by_id = {b.id: b.text for b in job.bullets}
    texts = texts or {}
    return ResumeEntry(
        id=job.id, heading=job.company, subheading=job.title,
        right=f"{job.start} – {job.end}", right_sub=job.location,
        bullets=[texts.get(i, by_id[i]) for i in ids if i in by_id],
        bullet_ids=[i for i in ids if i in by_id],
    )


def project_entry(project: Project, bullet_ids: list[str] | None = None, texts: dict[str, str] | None = None) -> ResumeEntry:
    ids = bullet_ids or [b.id for b in project.bullets]
    by_id = {b.id: b.text for b in project.bullets}
    texts = texts or {}
    return ResumeEntry(
        id=project.id, heading=project.name, subheading=project.stack_line, url=project.url,
        bullets=[texts.get(i, by_id[i]) for i in ids if i in by_id],
        bullet_ids=[i for i in ids if i in by_id],
    )


def ordered_skills(profile: Profile, order: list[str], highlight: list[str] | None = None) -> list[SkillGroup]:
    """Skill groups in `order` (unknown categories appended), highlighted items first within each group."""
    rank = {c: i for i, c in enumerate(order)}
    groups = sorted(profile.skills, key=lambda g: rank.get(g.category, len(rank)))
    hl = {h.lower() for h in (highlight or [])}
    out = []
    for g in groups:
        first = [s for s in g.items if s.lower() in hl]
        out.append(SkillGroup(category=g.category, items=first + [s for s in g.items if s.lower() not in hl]))
    return out


def track_order(bullets: list[Bullet], track: str) -> list[str]:
    """Bullet ids with the ones tagged for `track` first (stable otherwise)."""
    return [b.id for b in sorted(bullets, key=lambda b: track not in b.tags)]


def base_resume(profile: Profile, track: str, contact: list[str]) -> ResumeDoc:
    """The untailored resume for a track: every job, the track's projects, the track's skill order."""
    spec = profile.tracks[track]
    projects = [p for p in (profile.project(pid) for pid in spec.projects) if p]
    return ResumeDoc(
        name=profile.name,
        headline=spec.headline,
        contact=[c for c in contact if c],
        links=profile.links,
        summary=profile.track_summary(track),
        skills=ordered_skills(profile, spec.skill_order),
        experience=[job_entry(j, track_order(j.bullets, track)) for j in profile.experience],
        projects=[project_entry(p, track_order(p.bullets, track)) for p in projects],
        education=profile.education,
        achievements=profile.achievements,
    )
