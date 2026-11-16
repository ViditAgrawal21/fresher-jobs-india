"""Find copies of the same job across (and within) sources.

Three rules, merged with union-find so A~B and B~C puts A, B, C in one group:
  1. same canonical URL
  2. same normalized company + title + city
  3. same company, overlapping city, same level markers (I/II, intern, trainee...)
     and near-identical titles (rapidfuzz token_sort_ratio >= TITLE_THRESHOLD)

token_sort_ratio and not token_set_ratio on purpose: token_set_ratio scores
"Analyst" vs "Analyst II" as 100 (one is a subset of the other), which would merge
different levels of the same role.

Duplicates are marked (jobs.duplicate_of), never deleted.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations

from rapidfuzz import fuzz

from core.taxonomy import cities_in
from pipeline.normalize import title_norm

# 90 merged "Staff Software Engineer" with "Staff Software AIML Engineer" (score 90.2);
# real reposts ("Full-Stack" vs "FullStack", appended city) score 95+.
TITLE_THRESHOLD = 92

# Lower wins when picking which copy to show. Company boards are the original posting.
SOURCE_PRIORITY = {"greenhouse": 0, "lever": 0, "ashby": 0, "smartrecruiters": 0, "adzuna": 1, "jooble": 2}

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class _UnionFind:
    def __init__(self, items):
        self.parent = {i: i for i in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


_LEVEL_TOKENS = {"i", "ii", "iii", "iv", "1", "2", "3", "intern", "internship", "trainee", "junior", "jr", "apprentice"}


def _level(title: str) -> frozenset[str]:
    """Level markers must match exactly; "Product Analyst" vs "Product Analyst II" scores 90.9
    on token_sort_ratio, which is over the threshold but is a different job."""
    return frozenset(w for w in title.split() if w in _LEVEL_TOKENS)


def _cities_compatible(a: set[str], b: set[str]) -> bool:
    # A post with no recognisable city (e.g. just "India") can match any city.
    return not a or not b or bool(a & b)


def find_duplicates(rows: list[dict]) -> dict[int, int | None]:
    """rows need: id, source, canonical_url, company_norm, title, location_raw, desc_len, first_seen_at.

    Returns {id: id_of_kept_copy} for duplicates and {id: None} for kept rows.
    """
    if not rows:
        return {}
    uf = _UnionFind(r["id"] for r in rows)
    prepared = [
        {**r, "_title": title_norm(r["title"]), "_cities": cities_in(r["location_raw"])}
        for r in rows
    ]

    by_url: dict[str, list[int]] = defaultdict(list)
    by_key: dict[tuple, list[int]] = defaultdict(list)
    by_company: dict[str, list[dict]] = defaultdict(list)
    for r in prepared:
        by_url[r["canonical_url"]].append(r["id"])
        # "Unknown" company means the source didn't say; two unknown "Data Entry Operator"
        # posts in Pune are probably different employers, so they never match on title.
        if r["company_norm"] and r["company_norm"] != "unknown":
            by_key[(r["company_norm"], r["_title"], frozenset(r["_cities"]))].append(r["id"])
            by_company[r["company_norm"]].append(r)

    for group in [*by_url.values(), *by_key.values()]:
        for other in group[1:]:
            uf.union(group[0], other)

    for group in by_company.values():
        for a, b in combinations(group, 2):
            if not _cities_compatible(a["_cities"], b["_cities"]) or _level(a["_title"]) != _level(b["_title"]):
                continue
            if fuzz.token_sort_ratio(a["_title"], b["_title"]) >= TITLE_THRESHOLD:
                uf.union(a["id"], b["id"])

    clusters: dict[int, list[dict]] = defaultdict(list)
    for r in prepared:
        clusters[uf.find(r["id"])].append(r)

    result: dict[int, int | None] = {}
    for members in clusters.values():
        keeper = min(
            members,
            key=lambda r: (
                SOURCE_PRIORITY.get(r["source"], 9),
                -(r.get("desc_len") or 0),
                r.get("first_seen_at") or _EPOCH,
                r["id"],
            ),
        )
        for r in members:
            result[r["id"]] = None if r["id"] == keeper["id"] else keeper["id"]
    return result


def changed(rows: list[dict], result: dict[int, int | None]) -> list[tuple[int, int | None]]:
    """Only the rows whose duplicate_of actually needs updating."""
    return [(r["id"], result[r["id"]]) for r in rows if r.get("duplicate_of") != result[r["id"]]]
