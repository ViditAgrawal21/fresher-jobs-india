"""Get a job's full description, title, company and location from its link.

Company job boards (Greenhouse, Lever, Ashby, SmartRecruiters, Oracle) are asked through their
APIs, which return the whole ad even when the careers page is a JavaScript app. Any other page
is downloaded and turned into text. LinkedIn and Naukri block this: paste their text instead.
"""

from __future__ import annotations

import html
from urllib.parse import urlparse

import httpx

from jobhunt.verify import _date, ats_ref

UA = {"User-Agent": "Mozilla/5.0 (personal job application helper)"}


class FetchError(Exception):
    pass


def _company_from_slug(board: str) -> str:
    from jobhunt import discover as d
    from pipeline.sources import boards

    for table in (d.EXTRA_GREENHOUSE, d.EXTRA_LEVER, d.EXTRA_ASHBY, d.EXTRA_SMARTRECRUITERS, boards.GREENHOUSE,
                  boards.LEVER, boards.ASHBY, boards.SMARTRECRUITERS):
        if board in table:
            return table[board]
    return board.replace("-", " ").title()


def fetch_job(url: str, lead_key: str = "", client: httpx.Client | None = None) -> dict:
    """{company, role, location, jd, posted, url}. Raises FetchError with a reason a person can act on."""
    from pipeline.normalize import html_to_text

    host = urlparse(url or "").netloc.lower()
    if not str(url or "").startswith("http"):
        raise FetchError("That doesn't look like a link. Paste the full address, starting with https://")
    if "linkedin.com" in host or "naukri.com" in host:
        raise FetchError("LinkedIn and Naukri don't allow this. Open the job, copy the description, and paste it in.")
    own = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=True, headers=UA)
    try:
        ref = ats_ref(url, lead_key)
        if ref is None:
            r = client.get(url)
            r.raise_for_status()
            text = html_to_text(r.text)
            if len(text) < 400:
                raise FetchError("That page has almost no text (it probably loads the job with JavaScript). "
                                 "Copy the description from your browser and paste it in.")
            return {"company": "", "role": "", "location": "", "jd": text, "posted": None, "url": url}
        ats, board, job = ref
        if ats == "greenhouse":
            j = _get(client, f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job}")
            return {"company": j.get("company_name") or _company_from_slug(board), "role": j.get("title", ""),
                    "location": (j.get("location") or {}).get("name", ""),
                    "jd": html_to_text(html.unescape(j.get("content") or "")),
                    "posted": _date(j.get("first_published")), "url": url}
        if ats == "lever":
            j = _get(client, f"https://api.lever.co/v0/postings/{board}/{job}")
            cats = j.get("categories") or {}
            # Some postings only fill the HTML fields (description, additional), not the plain-text ones.
            parts = [j.get("descriptionPlain") or html_to_text(j.get("description") or "")]
            parts += [f"{b.get('text', '')}\n{html_to_text(b.get('content', ''))}" for b in j.get("lists") or []]
            parts.append(j.get("additionalPlain") or html_to_text(j.get("additional") or ""))
            loc = " · ".join(x for x in (cats.get("location") or "", j.get("workplaceType") or "") if x)
            return {"company": _company_from_slug(board), "role": j.get("text", ""), "location": loc,
                    "jd": "\n".join(parts), "posted": _date(j.get("createdAt")), "url": url}
        if ats == "ashby":
            data = _get(client, f"https://api.ashbyhq.com/posting-api/job-board/{board}")
            j = next((x for x in data.get("jobs", []) if str(x.get("id")) == job), None)
            if j is None:
                raise FetchError("That job is no longer on the company's job board.")
            loc = " · ".join(x for x in (j.get("location") or "", j.get("workplaceType") or "") if x)
            return {"company": _company_from_slug(board), "role": j.get("title", ""), "location": loc,
                    "jd": j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml") or ""),
                    "posted": _date(j.get("publishedAt")), "url": url}
        if ats == "smartrecruiters":
            j = _get(client, f"https://api.smartrecruiters.com/v1/companies/{board}/postings/{job}")
            sections = (j.get("jobAd") or {}).get("sections") or {}
            jd = "\n".join(html_to_text((sections.get(k) or {}).get("text", ""))
                           for k in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"))
            loc = j.get("location") or {}
            where = ", ".join(x for x in (loc.get("city"), loc.get("country", "").upper()) if x)
            return {"company": (j.get("company") or {}).get("name") or board, "role": j.get("name", ""),
                    "location": where + (" · remote" if loc.get("remote") else " · hybrid" if loc.get("hybrid") else ""),
                    "jd": jd, "posted": _date(j.get("releasedDate")), "url": url}
        if ats == "oracle":
            host_, site = board.split("|", 1)
            data = _get(client, f"https://{host_}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails",
                        params={"expand": "all", "onlyData": "true", "finder": f'ById;Id="{job}",siteNumber={site}'})
            items = data.get("items") or []
            if not items:
                raise FetchError("That job is no longer on the company's careers site.")
            j = items[0]
            jd = "\n".join(html_to_text(j.get(k) or "") for k in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr",
                                                                    "ExternalQualificationsStr", "CorporateDescriptionStr"))
            loc = " · ".join(x for x in (j.get("PrimaryLocation") or "", j.get("WorkplaceType") or "") if x)
            return {"company": "", "role": j.get("Title", ""), "location": loc, "jd": jd,
                    "posted": _date(j.get("ExternalPostedStartDate")), "url": url}
    except httpx.HTTPError as exc:
        raise FetchError(f"Couldn't download the job ({type(exc).__name__}). Check the link, or paste the description.") from exc
    finally:
        if own:
            client.close()
    raise FetchError("Unsupported link")


def _get(client: httpx.Client, url: str, **kw) -> dict:
    r = client.get(url, **kw)
    if r.status_code == 404:
        raise FetchError("That job is no longer on the company's job board (it has closed).")
    r.raise_for_status()
    return r.json()
