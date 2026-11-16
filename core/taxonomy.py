"""Single source of truth for tag values.

The tagger prompt, the Pydantic output schema, the API filters and the eval
scorer all read from here, so adding a role or city is a one-file change
(plus a re-tag).
"""

import re

# role -> what belongs in it. The descriptions go straight into the prompt.
ROLES: dict[str, str] = {
    "Software Engineering": "SDE, backend/frontend/full-stack, mobile, QA/testing, DevOps, cloud, IT/system admin",
    "Data & Analytics": "data/business/product analyst, BI, data engineering, MIS, research analyst working with data",
    "AI/ML": "machine learning, data scientist, AI/GenAI engineer, computer vision, NLP, AI research",
    "Product Management": "product manager, APM, product owner",
    "Design": "UI/UX, product, graphic, visual, motion design",
    "Marketing": "digital/performance/social media marketing, SEO, growth, brand",
    "Content & Communications": "content writing, copywriting, editing, PR, journalism, video editing",
    "Sales & Business Development": "sales, inside sales, BD, account executive, pre-sales, counsellor selling courses",
    "Customer Support & Success": "customer support/service, customer success, helpdesk, call centre, chat support",
    "Operations": "business/process ops, supply chain, logistics, back office, admin, project coordination",
    "Finance & Accounting": "accounts, audit, tax, FP&A, banking ops, credit, risk, payroll accounting",
    "HR & Recruiting": "HR generalist, talent acquisition, recruiting, people ops",
    "Consulting & Strategy": "management/strategy consulting, corporate strategy, founder's office",
    "Core Engineering": "mechanical, electrical, electronics, civil, chemical, manufacturing, embedded hardware",
    "Healthcare & Life Sciences": "pharma, clinical research, medical, lab, biotech",
    "Education & Teaching": "teacher, tutor, trainer, academic content (not sales counsellors)",
    "Legal & Compliance": "legal, compliance, company secretary, regulatory",
    "Other": "anything that clearly fits none of the above",
}

# Canonical city -> lowercase aliases that may appear in raw location strings.
CITIES: dict[str, list[str]] = {
    "Bengaluru": ["bengaluru", "bangalore", "blr"],
    "Mumbai": ["mumbai", "bombay"],
    "Navi Mumbai": ["navi mumbai"],
    "Thane": ["thane"],
    "Delhi": ["delhi", "new delhi", "delhi ncr", "ncr"],
    "Gurugram": ["gurugram", "gurgaon"],
    "Noida": ["noida", "greater noida"],
    "Ghaziabad": ["ghaziabad"],
    "Faridabad": ["faridabad"],
    "Hyderabad": ["hyderabad", "secunderabad"],
    "Pune": ["pune", "pimpri", "chinchwad", "hinjewadi"],
    "Chennai": ["chennai", "madras"],
    "Kolkata": ["kolkata", "calcutta"],
    "Ahmedabad": ["ahmedabad", "gandhinagar"],
    "Jaipur": ["jaipur"],
    "Kochi": ["kochi", "cochin", "ernakulam"],
    "Thiruvananthapuram": ["thiruvananthapuram", "trivandrum"],
    "Chandigarh": ["chandigarh"],
    "Mohali": ["mohali"],
    "Indore": ["indore"],
    "Bhopal": ["bhopal"],
    "Coimbatore": ["coimbatore"],
    "Madurai": ["madurai"],
    "Bhubaneswar": ["bhubaneswar", "bhubaneshwar"],
    "Lucknow": ["lucknow"],
    "Nagpur": ["nagpur"],
    "Nashik": ["nashik", "nasik"],
    "Mysuru": ["mysuru", "mysore"],
    "Mangaluru": ["mangaluru", "mangalore"],
    "Vadodara": ["vadodara", "baroda"],
    "Surat": ["surat"],
    "Visakhapatnam": ["visakhapatnam", "vizag"],
    "Vijayawada": ["vijayawada"],
    "Goa": ["goa", "panaji", "margao"],
    "Patna": ["patna"],
    "Guwahati": ["guwahati"],
    "Dehradun": ["dehradun"],
    "Ranchi": ["ranchi"],
    "Raipur": ["raipur"],
    "Ludhiana": ["ludhiana"],
}

# Not cities, but valid answers for "where is this job".
REMOTE = "Remote"
PAN_INDIA = "Pan India"  # post says many/all cities without a usable list
UNSPECIFIED = "Unspecified"  # post gives no city at all (e.g. location is just "India")
OTHER_CITY = "Other"  # a real city that isn't in the list above

CITY_VALUES: list[str] = [*CITIES, REMOTE, PAN_INDIA, UNSPECIFIED, OTHER_CITY]
ROLE_VALUES: list[str] = list(ROLES)

_alias_to_city = {alias: city for city, aliases in CITIES.items() for alias in aliases}
# Longest aliases first so "navi mumbai" wins over "mumbai" and "new delhi" over "delhi".
_alias_re = re.compile(
    r"\b(" + "|".join(re.escape(a) for a in sorted(_alias_to_city, key=len, reverse=True)) + r")\b"
)
_remote_re = re.compile(r"\b(remote|work from home|wfh)\b")


def cities_in(text: str) -> set[str]:
    """Canonical cities mentioned in a free-text location. Rule-based, used for dedup keys and eval."""
    text = (text or "").lower()
    found = {_alias_to_city[m] for m in _alias_re.findall(text)}
    # "Navi Mumbai" contains "mumbai" -- the regex already prefers the longer alias, nothing to undo.
    if _remote_re.search(text):
        found.add(REMOTE)
    return found


def strip_cities(text: str) -> str:
    """Remove city names/aliases from text (expects lowercase)."""
    return _alias_re.sub(" ", text)


def normalize_city_label(label: str) -> str | None:
    """Map a hand-typed city ("bangalore", "Gurgaon") to its canonical value."""
    label = label.strip()
    if not label:
        return None
    for value in CITY_VALUES:
        if value.lower() == label.lower():
            return value
    hits = cities_in(label)
    return next(iter(hits)) if len(hits) == 1 else OTHER_CITY
