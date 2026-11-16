from datetime import datetime, timezone

from pipeline.dedup import changed, find_duplicates
from pipeline.normalize import canonical_url, company_norm

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


def row(id, source, title, company, location, url=None, desc_len=500, duplicate_of=None):
    return {
        "id": id,
        "source": source,
        "canonical_url": canonical_url(url or f"https://example.com/{source}/{id}"),
        "company_norm": company_norm(company),
        "title": title,
        "location_raw": location,
        "desc_len": desc_len,
        "first_seen_at": T0,
        "duplicate_of": duplicate_of,
    }


def test_same_job_on_aggregator_and_company_board_keeps_company_board():
    rows = [
        row(1, "adzuna", "Social Media Intern", "Swiggy Pvt Ltd", "Bangalore, Karnataka"),
        row(2, "smartrecruiters", "Social Media Intern - Bengaluru", "Swiggy", "Bengaluru, India"),
    ]
    result = find_duplicates(rows)
    assert result == {2: None, 1: 2}


def test_different_levels_are_not_merged():
    rows = [
        row(1, "greenhouse", "Product Analyst", "Glance", "Bengaluru"),
        row(2, "greenhouse", "Product Analyst II", "Glance", "Bengaluru"),
    ]
    assert find_duplicates(rows) == {1: None, 2: None}


def test_same_title_different_city_is_not_duplicate():
    rows = [
        row(1, "adzuna", "Customer Support Executive", "Teleperformance", "Mumbai"),
        row(2, "adzuna", "Customer Support Executive", "Teleperformance", "Jaipur"),
    ]
    assert find_duplicates(rows) == {1: None, 2: None}


def test_unknown_city_matches_known_city():
    rows = [
        row(1, "adzuna", "Graduate Trainee Engineer", "Bosch", "India", desc_len=200),
        row(2, "adzuna", "Graduate Trainee Engineer", "Bosch Limited", "Bengaluru", desc_len=900),
    ]
    assert find_duplicates(rows) == {2: None, 1: 2}


def test_same_url_is_duplicate_even_if_titles_differ():
    rows = [
        row(1, "adzuna", "Data Analyst", "Acme", "Pune", url="https://x.com/j/9?utm_source=a"),
        row(2, "jooble", "Analyst - Data", "Acme Corp", "Pune", url="https://x.com/j/9"),
    ]
    assert find_duplicates(rows)[2] == 1


def test_unknown_company_never_fuzzy_matches():
    rows = [
        row(1, "adzuna", "Data Entry Operator", "Unknown", "Pune"),
        row(2, "adzuna", "Data Entry Operator", "Unknown", "Pune"),
    ]
    assert find_duplicates(rows) == {1: None, 2: None}


def test_transitive_groups():
    rows = [
        row(1, "adzuna", "Associate Data Scientist", "NielsenIQ", "Chennai"),
        row(2, "jooble", "Associate Data Scientist (Chennai)", "NielsenIQ India", "Chennai, Tamil Nadu"),
        row(3, "smartrecruiters", "Associate Data Scientist", "NielsenIQ", "Chennai, India"),
    ]
    assert find_duplicates(rows) == {3: None, 1: 3, 2: 3}


def test_changed_only_returns_updates():
    rows = [row(1, "adzuna", "A", "X", "Pune", duplicate_of=None), row(2, "adzuna", "A", "X", "Pune", duplicate_of=1)]
    assert changed(rows, find_duplicates(rows)) == []
