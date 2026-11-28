"""Where a job is, and whether it suits you.

Your order of preference (`rank`, lower is better):
  0  Remote, India: work from home anywhere in India
  1  Remote, open to India: worldwide, "anywhere", APAC or Asia roles from companies in the
     UK, US or elsewhere, in English
  2  Hybrid in a preferred city (PREFERRED_CITIES, default Pune, Mumbai, Bengaluru)
  3  In the office in a preferred city
  4  (wider search only) other Indian cities
Everything else is left out: offices abroad, remote roles limited to another country ("US
only", "must reside in the UK", "authorized to work in the US"), and other Indian cities
unless you ask for a wider search.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CITY_ALIASES = {
    "Bengaluru": ("bengaluru", "bangalore"), "Mumbai": ("mumbai", "navi mumbai", "thane", "bombay"),
    "Pune": ("pune",), "Hyderabad": ("hyderabad",), "Chennai": ("chennai",), "Gurugram": ("gurugram", "gurgaon"),
    "Noida": ("noida",), "Delhi": ("new delhi", "delhi"), "Kolkata": ("kolkata",), "Ahmedabad": ("ahmedabad",),
    "Kochi": ("kochi", "cochin"), "Jaipur": ("jaipur",), "Indore": ("indore",), "Coimbatore": ("coimbatore",),
}
# "Bangalore, IN": a country code after a comma, matched case-sensitively so the word "in" doesn't count.
_INDIA = re.compile(r"\bindia\b|(?-i:,\s*IN\b)|" + "|".join(rf"\b{a}\b" for names in CITY_ALIASES.values() for a in names), re.I)
_REMOTE = re.compile(r"\bremote\b|work from home|\bwfh\b|\banywhere\b|worldwide|distributed|home[- ]based", re.I)
_HYBRID = re.compile(r"\bhybrid\b", re.I)
# Regions that include India, or say the location doesn't matter.
_OPEN = re.compile(r"\bapac\b|\basia\b|asia[- ]pacific|worldwide|\banywhere\b|\bglobal(?:ly)?\b|\bist\b"
                   r"|[gu]tc ?\+ ?5:?30", re.I)
# Words that are about the arrangement, not a place: left over after them means a named place.
_NOT_PLACE = re.compile(r"\bremote\b|work from home|\bwfh\b|\bhybrid\b|on-?site|\boffice\b|full[- ]time|distributed"
                        r"|home[- ]based|\bfirst\b|\bonly\b|\bfriendly\b|\bjobs?\b|\bteam\b|[^a-z]+", re.I)
_PLACE = "us|usa|u\\.s\\.?|united states|america|uk|u\\.k\\.|united kingdom|britain|england|canada|europe|eu|emea|germany" \
         "|france|spain|netherlands|poland|ireland|latam|latin america|americas|north america|australia|new zealand" \
         "|brazil|mexico|singapore|japan|israel|switzerland|sweden|portugal"
# A remote role limited to another country, as job ads usually say it.
_RESTRICTED = re.compile(
    rf"\b(?:{_PLACE})\b[- ]?(?:only|based (?:candidates|applicants) only)\b"
    rf"|(?:must|need to|required to|should|you'll need to)\s+(?:be\s+)?(?:currently\s+)?(?:based|located|living|reside|live|residing)"
    rf"\s+(?:in|within)\s+(?:the\s+)?(?:{_PLACE})\b"
    rf"|(?:open|available)\s+(?:only\s+)?to\s+(?:candidates|applicants|residents)\s+(?:based\s+|located\s+)?(?:in|from)\s+(?:the\s+)?(?:{_PLACE})\b"
    rf"|(?:authori[sz]ed|eligible|legally (?:able|permitted))\s+to\s+work\s+in\s+(?:the\s+)?(?:us|usa|united states|uk|united kingdom|canada|eu|europe)\b"
    rf"|\bus citizen|\bgreen card\b|security clearance|\bw-?2\b|right to work in the uk",
    re.I,
)


@dataclass
class Where:
    ok: bool
    rank: int
    label: str


def preferred(cities: str | list[str]) -> list[str]:
    names = [c.strip() for c in (cities.split(",") if isinstance(cities, str) else cities) if c.strip()]
    out = []
    for n in names:
        canon = next((k for k, al in CITY_ALIASES.items() if n.lower() in al or n.lower() == k.lower()), n.title())
        out.append(canon)
    return out


def _city(text: str, cities: list[str]) -> str | None:
    """The first preferred city named in `text`, in your order of preference."""
    low = text.lower()
    for c in cities:
        for alias in CITY_ALIASES.get(c, (c.lower(),)):
            if re.search(rf"\b{re.escape(alias)}\b", low):
                return c
    return None


def _any_indian_city(text: str) -> str | None:
    return _city(text, list(CITY_ALIASES))


def classify(location: str, text: str = "", cities: str | list[str] = "Pune, Mumbai, Bengaluru",
             remote_abroad: bool = True, other_cities: bool = False) -> Where:
    """`location` is the job's location line (with "remote"/"hybrid" and the country, if the board gives
    them); `text` is the description, read for "US only"-style limits on remote roles."""
    loc = " ".join((location or "").split())
    prefs = preferred(cities)
    india = bool(_INDIA.search(loc))
    if _REMOTE.search(loc):
        if india:
            return Where(True, 0, "Remote (India)")
        if not remote_abroad:
            return Where(False, 9, "Remote, outside India")
        # Limits stated in the ad count unless it also names India or a region that includes it.
        limited = _RESTRICTED.search(f"{loc}\n{text[:30000]}")
        # The ad's own words beat a board's "Worldwide" tag; naming India or APAC lifts the limit.
        open_region = re.search(r"\bindia\b|\bapac\b|asia[- ]pacific", f"{loc}\n{text[:30000]}", re.I)
        if limited and not open_region:
            return Where(False, 9, "Remote, limited to another country")
        if _OPEN.search(loc):
            region = "APAC" if re.search(r"\bapac\b|asia", loc, re.I) else "worldwide"
            return Where(True, 1, f"Remote ({region})")
        leftover = _NOT_PLACE.sub(" ", loc).strip()
        if leftover:  # "Remote, United States", "Remote - Canada": a remote job for that country
            return Where(False, 9, f"Remote, {leftover}")
        return Where(True, 1, "Remote (country not stated)")
    city = _city(loc, prefs)
    if city:
        return Where(True, 2, f"Hybrid · {city}") if _HYBRID.search(loc) else Where(True, 3, f"{city} (office)")
    if india:
        other = _any_indian_city(loc) or "India"
        if other_cities:
            return Where(True, 4, f"{'Hybrid · ' if _HYBRID.search(loc) else ''}{other} (wider search)")
        return Where(False, 8, f"{other}: not one of your cities")
    return Where(False, 9, "Outside India")


_EN = {"the", "and", "to", "of", "you", "with", "for", "we", "our", "in", "is", "are", "will", "your", "on", "as"}
_OTHER = {"und", "der", "die", "das", "mit", "wir", "für", "sie", "ist", "de", "la", "le", "les", "des", "et", "pour",
          "nous", "vous", "para", "con", "los", "las", "el", "una", "que", "por", "het", "een", "van", "voor", "och"}


def is_english(text: str) -> bool:
    """True for an English job ad. Short texts get the benefit of the doubt."""
    words = re.findall(r"[a-zà-ÿ]+", (text or "").lower()[:20000])
    if len(words) < 60:
        return True
    en = sum(w in _EN for w in words)
    other = sum(w in _OTHER for w in words)
    return en >= 10 and en >= 2 * other
