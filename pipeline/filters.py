"""Cheap rule-based filters that run before anything costs money.

They only throw away the obvious: non-India postings from global company boards and
clearly senior titles. "Is this really for freshers?" is decided by the LLM, because
titles like "Associate" or "Analyst II" can't be judged by keywords alone.
"""

import re

from core.taxonomy import cities_in

_INDIA_RE = re.compile(
    r"\b(india|karnataka|maharashtra|telangana|tamil nadu|haryana|uttar pradesh|west bengal|gujarat|"
    r"rajasthan|kerala|punjab|madhya pradesh|odisha|andhra pradesh|delhi ncr)\b",
    re.I,
)

_SENIOR_RE = re.compile(
    r"\b(senior|sr\.?|lead(?!\s*gen)|manager|director|principal|head|vp|vice president|"
    r"architect|chief|president|avp)\b",
    re.I,
)
# "Junior Architect", "Lead Generation Intern" -- an explicit entry-level word wins.
# (Deliberately not "staff" or "partner": Staff Nurse and Delivery Partner are fresher roles in India.)
_ENTRY_RE = re.compile(r"\b(intern|internship|trainee|fresher|freshers|junior|graduate|apprentice|entry[- ]level)\b", re.I)


def is_india(location: str) -> bool:
    if not location:
        return False
    return bool(_INDIA_RE.search(location)) or bool(cities_in(location) - {"Remote"})


def looks_senior(title: str) -> bool:
    title = title or ""
    if _ENTRY_RE.search(title):
        return False
    return bool(_SENIOR_RE.search(title))


# Placeholder listings that some employers' test accounts leave on aggregators
# ("demo" by company "Test" was in the hand-checked sample; build log #12).
_PLACEHOLDER = {"demo", "test", "testing", "sample", "dummy", "test job", "test post", "test company", "na", "n/a"}


def is_placeholder_post(title: str, company: str) -> bool:
    t, c = (title or "").strip().lower(), (company or "").strip().lower()
    return t in _PLACEHOLDER or c in _PLACEHOLDER
