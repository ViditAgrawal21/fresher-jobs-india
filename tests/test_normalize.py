from core.models import RawPost
from core.taxonomy import cities_in, normalize_city_label
from pipeline.filters import is_india, looks_senior
from pipeline.normalize import canonical_url, company_norm, html_to_text, title_norm, to_row


def test_company_norm_strips_legal_suffixes():
    assert company_norm("Razorpay Software Private Limited") == "razorpay"
    assert company_norm("Swiggy Pvt. Ltd.") == "swiggy"
    assert company_norm("Bosch Group") == "bosch"
    # don't strip everything away
    assert company_norm("Global Technologies") == "global technologies"


def test_title_norm_drops_city_and_noise():
    assert title_norm("Data Analyst - Bengaluru") == "data analyst"
    assert title_norm("URGENT Hiring: Data Analyst (Bangalore)") == "data analyst"
    assert title_norm("Software Engineer Intern") == "software engineer intern"


def test_canonical_url_removes_tracking():
    a = canonical_url("https://Jobs.Lever.co/cred/123/?utm_source=linkedin&lever-source=x")
    b = canonical_url("https://jobs.lever.co/cred/123")
    assert a == b


def test_canonical_url_keeps_job_id_params():
    # Elastic/Stripe host Greenhouse jobs on their own site; gh_jid is the job id, not tracking.
    a = canonical_url("https://www.elastic.co/careers?gh_jid=111&gh_src=abc")
    b = canonical_url("https://www.elastic.co/careers?gh_jid=222")
    assert a != b
    assert a == canonical_url("https://www.elastic.co/careers?gh_jid=111")


def test_html_to_text_handles_escaped_and_plain():
    assert html_to_text("<p>Hello <b>world</b></p>") == "Hello world"
    assert html_to_text("plain   text\n here") == "plain text here"
    assert html_to_text(None) == ""


def test_cities_aliases():
    assert cities_in("Bangalore, Karnataka") == {"Bengaluru"}
    assert cities_in("Gurgaon / New Delhi") == {"Gurugram", "Delhi"}
    assert cities_in("Navi Mumbai, Maharashtra") == {"Navi Mumbai"}
    assert cities_in("Remote, India") == {"Remote"}
    assert cities_in("India") == set()
    assert normalize_city_label("bangalore") == "Bengaluru"
    assert normalize_city_label("Pan India") == "Pan India"


def test_is_india():
    assert is_india("Bengaluru, Karnataka, India")
    assert is_india("Pune")
    assert not is_india("Dublin, Ireland")
    assert not is_india("Remote - US")


def test_looks_senior():
    assert looks_senior("Senior Software Engineer")
    assert looks_senior("Sr. Analyst")
    assert looks_senior("Assistant Manager - Audit")
    assert looks_senior("Associate Director - Delivery")
    assert not looks_senior("Software Engineer Intern")
    assert not looks_senior("Lead Generation Intern")
    assert not looks_senior("Junior Architect")
    assert not looks_senior("Staff Nurse")
    assert not looks_senior("Graduate Trainee - Finance")


def test_to_row_keeps_none_description_for_known_posts():
    row = to_row(RawPost(source="smartrecruiters", source_id="x:1", url="https://a.b/1",
                         title="Process Associate", company="Bosch", location="Bengaluru", description=None))
    assert row["description"] is None and row["content_hash"] is None
    assert row["dedup_key"] == "bosch|process associate|Bengaluru"
