import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jobhunt import autoapply, autofill
from jobhunt.questions import QuestionBank, normalize
from jobhunt.settings import JobhuntSettings

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def _chromium() -> str:
    path = os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium")
    return path if os.path.exists(path) else ""


@pytest.fixture
def page():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        p = sync_api.sync_playwright().start()
        browser = p.chromium.launch(**({"executable_path": _chromium()} if _chromium() else {}))
    except Exception as exc:
        pytest.skip(str(exc))
    pg = browser.new_page()
    pg.set_default_timeout(3000)
    yield pg
    browser.close()
    p.stop()


SETTINGS = JobhuntSettings(_env_file=None, gmail_address="me@example.com", gmail_app_password="abcd efgh ijkl mnop")
ANSWERS = {"first_name": "Vidit", "email": "me@example.com", "willing_to_relocate": "Yes", "consent_to_terms": "Yes"}

FORM = """<!doctype html><html><body>
<form id="f" onsubmit="event.preventDefault(); document.body.innerHTML = '<h1>Thank you for applying!</h1>'">
<label for="fn">First Name *</label><input id="fn" required>
<label for="em">Email *</label><input id="em" type="email" required>
<fieldset><legend>Are you willing to relocate? *</legend>
  <label><input type="radio" name="reloc" value="y" required> Yes</label><label><input type="radio" name="reloc" value="n"> No</label></fieldset>
<div class="field"><label for="why">Why do you want to join Acme? *</label><textarea id="why" required></textarea></div>
<label><input type="checkbox" id="ok" required> I agree to the privacy policy *</label>
<button type="submit">Submit application</button>
</form></body></html>"""


def test_learns_unknown_questions_then_submits_once_answered(page, tmp_path):
    bank = QuestionBank(tmp_path / "questions.yaml")
    page.set_content(FORM)
    res = autofill.fill_page(page, ANSWERS, None, set(), bank=bank, company="Acme", role="SDE")
    assert page.eval_on_selector("input[name=reloc][value=y]", "e => e.checked")
    assert page.eval_on_selector("#ok", "e => e.checked")  # consent box ticked
    missing = autofill.missing_required(page)
    assert missing == ["Why do you want to join Acme?"]
    tab = autoapply.step(page, autoapply.TabState(), settings=SETTINGS, missing=missing, is_done=False, auto=True)
    assert tab.state == "needs_you" and "required question" in tab.note
    bank.save()
    saved = (tmp_path / "questions.yaml").read_text()
    assert "Why do you want to join {company}?" in saved and res["unknown"]

    # You answer it once in questions.yaml; the next form (any company) is submitted.
    saved = saved.replace("answer: ''", "answer: I like what {company} builds for its {role} team")
    (tmp_path / "questions.yaml").write_text(saved)
    bank = QuestionBank(tmp_path / "questions.yaml")
    page.set_content(FORM)
    autofill.fill_page(page, ANSWERS, None, set(), bank=bank, company="Acme", role="SDE")
    assert page.eval_on_selector("#why", "e => e.value") == "I like what Acme builds for its SDE team"
    tab = autoapply.step(page, autoapply.TabState(), settings=SETTINGS, missing=autofill.missing_required(page),
                         is_done=False, auto=True)
    assert tab.submits == 1
    page.wait_for_timeout(300)
    assert autofill.is_confirmation(page)


def test_without_submit_for_me_it_only_fills(page):
    page.set_content(FORM)
    tab = autoapply.step(page, autoapply.TabState(), settings=SETTINGS, missing=[], is_done=False, auto=False)
    assert tab.state == "waiting" and tab.submits == 0


MULTI = """<!doctype html><html><body>
<div id="p1"><label for="fn">First Name *</label><input id="fn" required>
  <button type="button" onclick="p1.hidden = true; p2.hidden = false">Next</button></div>
<div id="p2" hidden><label for="em">Email *</label><input id="em" required>
  <button type="button" onclick="document.body.innerHTML = '<p>Your application has been submitted.</p>'">Submit</button></div>
</body></html>"""


def test_multi_page_form_goes_next_then_submit(page, monkeypatch):
    monkeypatch.setattr(autoapply.time, "time", iter(range(0, 10_000, 10)).__next__)  # no waiting between clicks
    page.set_content(MULTI)
    tab, done = autoapply.TabState(), set()
    for _ in range(4):
        autofill.fill_page(page, ANSWERS, None, done)
        if autofill.is_confirmation(page):
            break
        autoapply.step(page, tab, settings=SETTINGS, missing=autofill.missing_required(page), is_done=False, auto=True)
        page.wait_for_timeout(200)
    assert tab.nexts == 1 and tab.submits == 1 and autofill.is_confirmation(page)


def test_stops_for_a_captcha_but_not_an_invisible_check(page):
    visible = '<iframe src="https://www.google.com/recaptcha/api2/bframe?k=x" width="300" height="300"></iframe>'
    badge = '<iframe src="https://www.google.com/recaptcha/api2/anchor?k=x&size=invisible" width="256" height="60"></iframe>'
    page.set_content(FORM.replace("</form>", "</form>" + visible))
    tab = autoapply.step(page, autoapply.TabState(), settings=SETTINGS, missing=[], is_done=False, auto=True)
    assert tab.state == "needs_you" and "CAPTCHA" in tab.note and tab.submits == 0
    page.set_content(FORM.replace("</form>", "</form>" + badge))
    tab = autoapply.step(page, autoapply.TabState(), settings=SETTINGS, missing=[], is_done=False, auto=True)
    assert tab.submits == 1


def test_sign_in_pages_wait_for_you(page):
    page.set_content('<label for="u">Email</label><input id="u"><label for="pw">Password</label>'
                     '<input id="pw" type="password"><button>Sign In</button>')
    tab = autoapply.step(page, autoapply.TabState(), settings=SETTINGS, missing=[], is_done=False, auto=True)
    assert tab.state == "needs_you" and "sign in" in tab.note


def test_security_code_from_email(page, monkeypatch):
    monkeypatch.setattr(autoapply, "security_code", lambda *a, **k: "8XkPq2Za")
    page.set_content('<label for="c">Security code</label><input id="c">'
                     '<button onclick="document.body.innerHTML=\\\'<h1>Thank you for applying</h1>\\\'">Submit application</button>')
    tab = autoapply.step(page, autoapply.TabState(), settings=SETTINGS, missing=[], is_done=False, auto=True)
    assert page.eval_on_selector("#c", "e => e.value") == "8XkPq2Za" and tab.submits == 1


@pytest.mark.parametrize("text,code", [
    ("Copy and paste this code into the security code field on your application: 8XkPq2Za\nThanks", "8XkPq2Za"),
    ("Your verification code is 482913. It expires in 10 minutes.", "482913"),
    ("Hello, thanks for applying to Acme.", None),
])
def test_extract_code(text, code):
    assert autoapply.extract_code(text) == code


def test_security_code_waits_for_a_new_email():
    now = datetime.now(timezone.utc)
    mails = [("Old code", "code 111111", now - timedelta(hours=2)),
             ("Your Acme security code", "Paste this code: Zz9Yy8Xx", now)]
    got = autoapply.security_code(SETTINGS, since=now - timedelta(minutes=5), fetch=lambda s, since: mails, timeout_s=1)
    assert got == "Zz9Yy8Xx"
    nobody = JobhuntSettings(_env_file=None, gmail_address="", candidate_email="", gmail_app_password="")
    assert autoapply.security_code(nobody, since=now) is None


def test_question_bank_matching(tmp_path):
    bank = QuestionBank(tmp_path / "q.yaml")
    bank.learn("How did you come to know about Sigmoid?", company="Sigmoid", kind="dropdown",
               choices=["LinkedIn", "Referral"], required=True)
    bank.learn("How did you come to know about Sigmoid? *", company="Sigmoid")
    assert len(bank.items) == 1 and bank.items[0]["ask"] == "How did you come to know about {company}?"
    bank.items[0]["answer"] = "LinkedIn"
    assert bank.answer("How did you come to know about Zenoti?", company="Zenoti") == "LinkedIn"
    assert bank.answer("Something else entirely", company="Zenoti") == ""
    assert normalize("Why Acme?*", "Acme") == "why {company}"


def test_closing_the_browser_is_not_an_error():
    assert autofill._closed(Exception("Page.wait_for_timeout: Target page, context or browser has been closed"))
    assert not autofill._closed(Exception("Timeout 4000ms exceeded"))


COMBO = """<!doctype html><html><body>
<ul id="phone-list" role="listbox" style="display:none"><li role="option">Afghanistan+93</li><li role="option">No Country+0</li></ul>
<label id="sl">Will you now or in the future require sponsorship? *</label>
<input id="sp" role="combobox" aria-labelledby="sl" aria-required="true" autocomplete="off">
<ul id="sp-list" role="listbox" hidden><li role="option">Yes</li><li role="option">No</li></ul>
<label id="al">I acknowledge the Candidate Privacy Notice *</label>
<select id="ack" aria-labelledby="al" required><option></option><option>I do not acknowledge</option><option>I acknowledge</option></select>
<li class="application-question"><div class="application-label"><div class="text">Expected CTC<span>✱</span></div></div>
  <div class="application-field"><input name="cards[abc][field0]" id="ctc" required></div></li>
<li class="application-question"><div class="application-label"><div class="text">How many years with React?✱</div></div>
  <div class="application-field"><ul><li><label><input type="radio" name="cards[abc][field1]" value="a">0-2</label></li>
  <li><label><input type="radio" name="cards[abc][field1]" value="b">2-4</label></li></ul></div></li>
<script>
const sp = document.getElementById('sp'), list = document.getElementById('sp-list');
sp.addEventListener('click', () => { list.hidden = false; sp.setAttribute('aria-controls', 'sp-list'); });
list.addEventListener('click', e => { sp.value = e.target.textContent; list.hidden = true; });
</script></body></html>"""


def test_dropdowns_lever_labels_consent_and_units(page, tmp_path):
    bank = QuestionBank(tmp_path / "q.yaml")
    page.set_content(COMBO)
    answers = dict(ANSWERS, needs_sponsorship="No", expected_ctc_lpa="16")
    res = autofill.fill_page(page, answers, None, set(), bank=bank, company="Acme")
    val = lambda sel: page.eval_on_selector(sel, "e => e.value")
    assert val("#sp") == "No"                      # its own list, not the hidden phone-country list
    assert page.eval_on_selector("#ack", "e => e.options[e.selectedIndex].text") == "I acknowledge"
    assert val("#ctc") == "16 LPA"                 # Lever label found; a unit added in a text box
    assert [u["label"] for u in res["unknown"]] == ["How many years with React?"]
    assert res["unknown"][0]["choices"] == ["0-2", "2-4"]


@pytest.mark.parametrize("label,key", [
    ("Please review the NDA and indicate your agreement by typing your full name below", "full_name"),
    ("What is your current country of residence?", "country"),
    ("Are you currently or have you ever been employed by Rubrik?", "worked_here_before"),
    ("Have you previously worked at or consulted for GitLab?", "worked_here_before"),
    ("Do you have hands-on experience with IAM?", None),
    ("Please list your most recent Employer here.", "current_company"),
    ("Please share the name of your current firm.", "current_company"),
    ("What is your current job title?", "current_title"),
    ("What are your compensation expectations for this role?", "expected_ctc_lpa"),
    ("Year of graduation", "education_end_year"),
    ("Gender Identity (Select one)", "gender"),
    ("opportunityLocationId", "location_preference"),
])
def test_more_rules(label, key):
    assert autofill.answer_for(label, {})[0] == key


def test_yes_no_as_sentences():
    opts = ["I have never been employed by Rubrik", "I am currently an employee", "I was previously a contractor"]
    assert autofill._pick(opts, "No", "worked_here_before") == 0
    assert autofill._pick(opts, "Yes", "worked_here_before") is None  # several "yes" answers: don't guess


LEVER_LOC = """<!doctype html><html><body><li class="application-question"><label>
<div class="application-label">Current location <span class="required">✱</span></div>
<div class="application-field"><input class="location-input" id="location-input" name="location" required>
<input id="selected-location" type="hidden" name="selectedLocation">
<div class="dropdown-results" id="res" hidden></div></div></label></li>
<div class="select__control"><input id="rs" role="combobox" aria-label="Pick one"><input tabindex="-1" aria-hidden="true" required style="opacity:0"></div>
<script>
const i = document.getElementById('location-input'), res = document.getElementById('res');
i.addEventListener('input', () => { res.hidden = !i.value;
  res.innerHTML = i.value === 'Pune' ? '<div class="dropdown-location">Pune, Maharashtra, IND</div><div class="dropdown-location">Puna, Una, IND</div>' : ''; });
res.addEventListener('click', e => { document.getElementById('selected-location').value = e.target.textContent; i.value = e.target.textContent; res.hidden = true; });
</script></body></html>"""


def test_lever_location_suggestions_and_hidden_helper_inputs(page):
    page.set_content(LEVER_LOC)
    autofill.fill_page(page, {"location": "Pune, Maharashtra, India"}, None, set())
    assert page.eval_on_selector("#selected-location", "e => e.value") == "Pune, Maharashtra, IND"
    assert autofill.missing_required(page) == []  # the invisible helper input isn't a question


def test_yes_never_maps_to_another_answer_when_no_is_a_choice():
    assert autofill._pick(["No", "Already in Bangalore"], "Yes", "willing_to_relocate") is None
    assert autofill._pick(["No", "Already in Bangalore"], "No", "willing_to_relocate") == 0


def test_a_ticked_radio_group_counts_as_answered_on_the_next_pass(page, tmp_path):
    page.set_content("""<li class="application-question"><div class="application-label">Experience in number of years ✱</div>
      <div class="application-field"><ul><li><label><input type="radio" name="x" value="a">2-4</label></li>
      <li><label><input type="radio" name="x" value="b">4-6</label></li></ul></div></li>""")
    bank, done = QuestionBank(tmp_path / "q.yaml"), set()
    autofill.fill_page(page, {"total_experience_years": "2.3"}, None, done, bank=bank)
    second = autofill.fill_page(page, {"total_experience_years": "2.3"}, None, done, bank=bank)
    assert page.eval_on_selector("input[value=a]", "e => e.checked") and second["unknown"] == [] and bank.items == []


def test_drafts_wait_for_review(tmp_path):
    bank = QuestionBank(tmp_path / "q.yaml")
    bank.learn("What excites you about {company}?", company="Fam", kind="text", required=True)
    bank.items[0].update(answer="Payments for teenagers", reviewed=False)
    assert bank.answer("What excites you about Fam?", company="Fam") == "" and len(bank.unanswered()) == 1
    bank.items[0]["reviewed"] = True
    assert bank.answer("What excites you about Fam?", company="Fam") == "Payments for teenagers"


@pytest.mark.parametrize("label,ans", [
    ("Do you have hands-on experience with React and Node.js?", "Yes"),
    ("Have you worked with PostgreSQL?", "Yes"),
    ("Have you worked hands-on with OIDC or OAuth 2.0?", ""),       # "or": which one? left for you
    ("Do you have experience with Kubernetes?", ""),               # not on your profile: never a guessed "No"
    ("Is your primary programming language Go?", ""),
    ("How many years of experience do you have with React?", ""),
])
def test_skill_questions(label, ans):
    skills = {"React", "Node.js", "PostgreSQL", "Python"}
    assert autofill.skill_answer(label, skills) == ans


def test_cgpa_and_office_rules():
    assert autofill.answer_for("CGPA (out of 10)", {"cgpa": "7.5"}) == ("cgpa", "7.5")
    assert autofill.answer_for("Are you comfortable working from our Pune office 3 days a week?", {"work_from_office": "Yes"})[0] == "work_from_office"
