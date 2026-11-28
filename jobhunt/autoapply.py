"""Submit applications for you, once every required question on the form is answered.

Each pass over an open application tab (see autofill.run, every 2 seconds):
  1. fill what it can (answers.yaml, questions.yaml, the tailored resume);
  2. done if the page says "thank you for applying";
  3. a CAPTCHA, a sign-in or account page, or required questions it can't answer: it stops on
     that tab and tells you why. You finish it in the same window, and the tab is still
     watched, so the job is marked Submitted when you do;
  4. a security code field (Greenhouse, Oracle): it reads the code from your Gmail
     (GMAIL_APP_PASSWORD) and enters it;
  5. otherwise it clicks Next on multi-page forms, or Submit on the last page.

It never solves CAPTCHAs, never creates accounts, and never runs on LinkedIn or Naukri (both
forbid automated applications). It tries Submit at most twice per form: if the form then shows
errors, the tab waits for you.
"""

from __future__ import annotations

import imaplib
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from jobhunt.settings import JobhuntSettings

log = logging.getLogger(__name__)

SUBMIT_TEXT = re.compile(r"^(submit( (my|your))?( application)?|send( my)? application|complete( my)? application"
                         r"|submit and apply|review and submit)$", re.I)
# "Apply now" opens the form, so it counts as a step forward, not the final submit.
NEXT_TEXT = re.compile(r"^(next|continue|save (and|&) continue|next step|proceed|save and next|apply( now)?"
                       r"|apply for this (job|position|role)|start (your )?application|i'?m interested)$", re.I)
NOT_OURS = re.compile(r"linkedin|indeed|google|seek|autofill|parse|upload|dropbox|search|sign ?in|log ?in|create account|"
                      r"subscribe|alert|share", re.I)
CODE_LABEL = re.compile(r"security code|verification code|one[- ]time (pass)?code|confirmation code|enter (the )?code", re.I)

# Visible CAPTCHA challenges (an invisible check needs nothing from you and isn't listed).
_PAGE_JS = r"""() => {
  const vis = e => !!e && (e.checkVisibility ? e.checkVisibility({ visibilityProperty: true, opacityProperty: true })
                                             : !!(e.offsetWidth || e.offsetHeight));
  const frames = [...document.querySelectorAll('iframe')].filter(vis).map(f => (f.src || '') + ' ' + (f.title || ''));
  // An invisible check (Greenhouse's reCAPTCHA badge) needs nothing from you, so it doesn't count.
  const captcha = frames.some(s => !/invisible/i.test(s) && /recaptcha\/(api2|enterprise)\/(bframe|anchor)|hcaptcha\.com.*(challenge|checkbox)|challenges\.cloudflare|turnstile|arkoselabs|funcaptcha/i.test(s))
    || /verify (that )?you are (a )?human|are you a robot|complete the captcha/i.test(document.body.innerText.slice(0, 20000));
  const password = [...document.querySelectorAll('input[type=password]')].some(vis);
  // Markers from an earlier pass (a page of the form now hidden) would point at the wrong button.
  document.querySelectorAll('[data-jh-btn]').forEach(b => b.removeAttribute('data-jh-btn'));
  document.querySelectorAll('[data-jh-code]').forEach(b => b.removeAttribute('data-jh-code'));
  const buttons = [...document.querySelectorAll('button, input[type=submit], [role=button], a[role=button]')]
    .filter(b => vis(b) && !b.disabled && b.getAttribute('aria-disabled') !== 'true')
    .map((b, n) => { b.dataset.jhBtn = String(n); return { n, text: (b.innerText || b.value || b.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim().slice(0, 60),
                                                           submit: b.type === 'submit' }; });
  const errors = [...document.querySelectorAll('[aria-invalid="true"], [class*="error" i]:not(script):not(style)')]
    .filter(vis).map(e => (e.getAttribute('aria-invalid') === 'true'
        ? ((e.labels && e.labels[0] && e.labels[0].innerText) || e.name || 'a field') + ': needs a valid answer'
        : (e.innerText || '').trim())).filter(t => t && t.length < 200);
  const code = [...document.querySelectorAll('input')].filter(vis).find(i => {
    const t = ((i.labels && [...i.labels].map(l => l.innerText).join(' ')) || i.getAttribute('aria-label') || i.placeholder || i.name || '');
    return /security code|verification code|one[- ]time|confirmation code|enter (the )?code|\bcode\b/i.test(t) && !(i.value || '').trim();
  });
  if (code) code.dataset.jhCode = '1';
  return { captcha, password, buttons, errors: [...new Set(errors)].slice(0, 8), code: !!code };
}"""


@dataclass
class TabState:
    """What auto-apply knows about one application tab."""

    state: str = "filling"           # filling | waiting | needs_you | submitted
    note: str = ""
    submits: int = 0
    nexts: int = 0
    last_click: float = 0.0
    code_tried: bool = False
    missing: list[str] = field(default_factory=list)

    def status(self) -> dict:
        return {"state": self.state, "note": self.note, "missing": self.missing[:12]}


def _click(page, n: int) -> None:
    page.locator(f'[data-jh-btn="{n}"]').first.click(timeout=5000)


def step(page, tab: TabState, *, settings: JobhuntSettings, missing: list[str], is_done: bool, auto: bool) -> TabState:
    """Decide and do the next thing on one tab after a fill pass."""
    if is_done:
        tab.state, tab.note = "submitted", "Submitted: the page confirmed it."
        return tab
    if not auto:
        tab.state, tab.note, tab.missing = "waiting", "Filled. Check it and click Submit yourself.", missing
        return tab
    info = page.evaluate(_PAGE_JS)
    if time.time() - tab.last_click < 6:  # give the last click time to load the next page or the result
        return tab
    if info["captcha"]:
        tab.state, tab.note = "needs_you", "A CAPTCHA appeared: solve it in this tab and click Submit."
        return tab
    if info["code"] and not tab.code_tried:
        tab.code_tried = True
        code = security_code(settings, since=datetime.now(timezone.utc) - timedelta(minutes=10))
        if code:
            box = page.locator('[data-jh-code="1"]').first
            if box.evaluate("e => e.maxLength") == 1:  # one box per digit (Oracle): type into them in turn
                box.click()
                page.keyboard.type(code, delay=80)
            else:
                box.fill(code)
            tab.note = "Entered the security code from your email."
            info = page.evaluate(_PAGE_JS)
        else:
            tab.state, tab.note = "needs_you", ("The site emailed you a code: enter it in this tab"
                                                + ("" if all(settings.gmail_login) else " (set GMAIL_APP_PASSWORD to have it entered for you)") + ".")
            return tab
    if info["password"]:
        tab.state, tab.note = "needs_you", ("This site needs you to sign in or create an account. Do it once in this window "
                                            "(it remembers you); autofill carries on afterwards.")
        return tab
    if missing:
        tab.state, tab.missing = "needs_you", missing
        tab.note = f"{len(missing)} required question(s) have no answer yet. Answer them here, or in questions.yaml for next time."
        return tab
    if tab.submits and info["errors"]:
        tab.state, tab.note = "needs_you", "The form shows errors after Submit: " + "; ".join(info["errors"][:3])
        return tab
    ours = [b for b in info["buttons"] if not NOT_OURS.search(b["text"])]
    submit = next((b for b in ours if SUBMIT_TEXT.match(b["text"])), None)
    nxt = next((b for b in ours if NEXT_TEXT.match(b["text"])), None)
    if submit and tab.submits < 2:
        _click(page, submit["n"])
        tab.submits += 1
        tab.last_click = time.time()
        tab.state, tab.note = "filling", "Submitted the form; waiting for the confirmation…"
    elif nxt and not submit and tab.nexts < 12:
        _click(page, nxt["n"])
        tab.nexts += 1
        tab.last_click = time.time()
        tab.state, tab.note = "filling", "Going to the next page of the form…"
    elif not submit and not nxt:
        tab.state, tab.note = "needs_you", "Couldn't find the Submit button on this page: finish it here."
    else:
        tab.state, tab.note = "needs_you", "Submit didn't go through twice: check the form in this tab."
    return tab


# ---------- the security code some application forms email you ----------

_CODE_NEAR = re.compile(r"code", re.I)
_CODE_TOKEN = re.compile(r"\b(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{4,10}\b|\b(?=[a-z]*[A-Z])(?=[A-Za-z]*[a-z])[A-Za-z]{6,10}\b")
_NOT_CODE = re.compile(r"^(?:\d{1,2}|20\d\d|19\d\d)$|^(?:http|https|www)$", re.I)


def extract_code(text: str) -> str | None:
    """The code in a "here is your security code" email: the first code-like word after "code"."""
    m = _CODE_NEAR.search(text or "")
    if not m:
        return None
    for tok in _CODE_TOKEN.findall(text[m.end(): m.end() + 600]):
        if not _NOT_CODE.match(tok) and not tok.lower().startswith(("http", "www")):
            return tok
    return None


def security_code(settings: JobhuntSettings, since: datetime, timeout_s: int = 120, poll_s: int = 8,
                  fetch=None) -> str | None:
    """Wait for the newest email with a code (Greenhouse, Oracle) and return the code."""
    address, password = settings.gmail_login
    if not (address and password):
        return None
    fetch = fetch or _recent_mail
    deadline = time.time() + timeout_s
    while True:
        try:
            for subject, body, received in fetch(settings, since):
                if received >= since and re.search(r"code|verif", subject + body[:2000], re.I):
                    code = extract_code(f"{subject}\n{body}")
                    if code:
                        return code
        except (imaplib.IMAP4.error, OSError) as exc:
            log.warning("couldn't read the code email: %s", exc)
            return None
        if time.time() + poll_s > deadline:
            return None
        time.sleep(poll_s)


def _recent_mail(settings: JobhuntSettings, since: datetime) -> list[tuple[str, str, datetime]]:
    from jobhunt.mailsync import parse_message

    address, password = settings.gmail_login
    out = []
    with imaplib.IMAP4_SSL(settings.imap_host) as imap:
        imap.login(address, password)
        imap.select("INBOX", readonly=True)
        _, data = imap.search(None, f'(SINCE "{since:%d-%b-%Y}")')
        for mid in reversed(data[0].split()[-15:]):
            _, msg = imap.fetch(mid, "(RFC822)")
            item = parse_message(msg[0][1])
            received = item.received if item.received.tzinfo else item.received.replace(tzinfo=timezone.utc)
            out.append((item.subject, item.body, received))
    return out
