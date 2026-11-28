"""Read recruiting emails from Gmail (IMAP) and update the tracker.

For every new email since the last sync:
  1. match it to one tracked application (company name in the sender, subject or opening lines),
  2. classify it by phrases (acknowledged / viewed / assessment / interview / offer / rejected),
  3. mark "Email Received? = Yes", store date/from/subject/signal, and move Status forward.

Rules: a status only moves forward; Accepted is never set by email; a rejection never
overwrites an Offer (that's logged as a conflict). Emails are data, never instructions;
nothing here acts on their content beyond these regexes.
"""

from __future__ import annotations

import email
import imaplib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from bs4 import BeautifulSoup

from jobhunt.tracker import STAGE, TERMINAL, Tracker

log = logging.getLogger(__name__)

# signal -> tracker status it implies
SIGNAL_STATUS = {
    "Acknowledged": "Acknowledged",
    "Viewed": "Viewed",
    "Assessment": "Assessment",
    "Interview": "Interviewing",
    "Offer": "Offer",
    "Rejected": "Rejected",
}

# Checked in this order: the first that matches wins. Rejections often also say
# "thank you for applying", so they are checked before acknowledgements.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Offer", re.compile(r"\b(?:pleased|happy|delighted|excited) to (?:offer|extend)|offer letter|letter of offer"
                         r"|letter of intent|\boffer of employment|compensation (?:details|breakup|break-up)", re.I)),
    ("Rejected", re.compile(r"not (?:be )?(?:moving|proceeding|progressing) (?:forward|further|ahead)"
                            r"|(?:move|moving|proceed|go) (?:forward|ahead) with (?:other|another) candidate"
                            r"|other candidates? whose|not (?:been )?(?:selected|shortlisted)|unable to (?:move|proceed|offer)"
                            r"|decided not to (?:proceed|move|progress)|regret to inform|position has been filled"
                            r"|will not be (?:progressing|proceeding)|not a (?:fit|match) (?:at this time|for this role)"
                            r"|unfortunately,? (?:we|after|at this|your)", re.I)),
    ("Interview", re.compile(r"schedul(?:e|ing) (?:an? |your )?(?:call|interview|chat|conversation|discussion|round)"
                             r"|interview (?:invite|invitation|scheduled|confirmation|slot|details)"
                             r"|invite you (?:to|for) (?:an? )?(?:interview|call|discussion|chat)|next round"
                             r"|technical (?:interview|round|discussion)|hiring manager (?:round|call|interview)"
                             r"|calendly\.com|book (?:a|your) (?:time|slot)|phone screen|screening call|virtual interview"
                             r"|onsite interview|shortlisted for", re.I)),
    ("Assessment", re.compile(r"\bassessment\b|coding (?:challenge|test|round|exercise)|take[- ]home|hackerrank\.com"
                              r"|codility|hackerearth|codesignal|testgorilla|mettl|imocha|online test|\bOA\b", re.I)),
    ("Viewed", re.compile(r"viewed your (?:application|profile|resume)|application (?:was |has been )?viewed"
                          r"|recruiter (?:has )?viewed|downloaded your (?:resume|cv)", re.I)),
    ("Acknowledged", re.compile(r"(?:received|receiving|reviewing) your application|thank(?:s| you) for (?:applying|your application"
                                r"|your interest)|application (?:has been |was )?(?:received|submitted)"
                                r"|your application (?:was )?sent to|successfully applied|application confirmation", re.I)),
]

# Senders that are about hiring even when the email has no clear signal phrase:
# ATS platforms and HR/talent mailboxes. Job portals (LinkedIn, Naukri...) also send
# promos and feed notifications, so they only count when a signal phrase matched.
_RECRUITING_SENDER = re.compile(
    r"greenhouse|lever\.co|ashbyhq|smartrecruiters|workday|myworkday|icims|bamboohr|zohorecruit|keka|darwinbox"
    r"|careers?|talent|recruit|hiring|\bhr\b|jobs?@|people@",
    re.I,
)
# Job portals and auto-apply tools whose "application sent / received" emails create a row.
_PORTAL = re.compile(r"linkedin|naukri|instahyre|wellfound|cutshort|hirist|foundit|iimjobs|indeed|aperture", re.I)
_ATS = re.compile(r"greenhouse|lever\.co|ashbyhq|smartrecruiters|workday|icims|bamboohr|zohorecruit|keka|darwinbox", re.I)
_APPLIED_TO = [
    re.compile(r"application (?:was |has been )?sent to (?P<company>[^,.!?|\n]{2,60})", re.I),
    re.compile(r"(?i:you|you've|you have) applied (?i:to|at|for) (?:the )?(?P<role>[^,.!?|\n]{3,80}?) (?:role |position )?at "
               r"(?P<company>[A-Z][^,.!?|\n]{1,60})"),
    re.compile(r"(?i:application) for (?:the )?(?P<role>[^,.!?|\n]{3,80}?) (?:role |position )?at (?P<company>[A-Z][^,.!?|\n]{1,60})"),
    re.compile(r"(?i:applying|applied|application) (?:to|at|with) (?P<company>[A-Z][^,.!?|\n]{1,60})"),
]
_SENDER_NOISE = re.compile(r"\b(?:careers?|hiring|talent|recruiting|recruitment|team|jobs|hr|people|no-?reply|notifications?)\b",
                           re.I)

_COMPANY_NOISE = re.compile(
    r"\b(?:pvt|private|ltd|limited|inc|llc|llp|corp|corporation|technologies|technology|tech|software|solutions"
    r"|services|labs|india|group|co)\b\.?",
    re.I,
)


@dataclass
class MailItem:
    message_id: str
    received: datetime
    sender: str          # "Name <addr>"
    subject: str
    body: str            # plain text, first few thousand characters


@dataclass
class Change:
    app_id: str
    company: str
    signal: str | None
    old_status: str | None
    new_status: str | None
    mail: MailItem
    note: str = ""


@dataclass
class SyncReport:
    changes: list[Change] = field(default_factory=list)
    conflicts: list[Change] = field(default_factory=list)
    unmatched: list[MailItem] = field(default_factory=list)
    ambiguous: list[tuple[MailItem, list[str]]] = field(default_factory=list)
    added: list[tuple[MailItem, dict]] = field(default_factory=list)


# ---------- matching and classification ----------

def company_key(name: str) -> str:
    """'Razorpay Software Pvt. Ltd.' -> 'razorpay'; 'Zomato / Blinkit' -> 'zomato / blinkit'."""
    s = _COMPANY_NOISE.sub(" ", (name or "").lower())
    s = re.sub(r"[^a-z0-9/&.+ ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip(" .")


def _contains(haystack: str, key: str) -> bool:
    if len(key) < 3:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", haystack) is not None


def match_application(mail: MailItem, apps: list) -> tuple[object | None, list[str]]:
    """The one tracked application this email is about, or (None, candidates) if none/ambiguous."""
    name, addr = parseaddr(mail.sender)
    domain = addr.split("@")[-1].lower() if "@" in addr else ""
    head = f"{name} {domain} {mail.subject}".lower()
    body = mail.body[:2500].lower()
    strong, weak = [], []
    for app in apps:
        key = company_key(app["company"] or "")
        if not key:
            continue
        compact = key.replace(" ", "")
        if _contains(head, key) or (compact and compact in domain.replace("-", "")):
            strong.append(app)
        elif _contains(body, key):
            weak.append(app)
    candidates = strong or weak
    companies = sorted({company_key(a["company"]) for a in candidates})
    if len(companies) == 1:
        # Same company, several roles: prefer the role named in the email, else the most recent application.
        same = candidates
        named = [a for a in same if a["role"] and a["role"].lower()[:25] in (mail.subject + " " + mail.body[:2500]).lower()]
        pick = (named or same)[-1]
        return pick, companies
    return None, companies


def classify(mail: MailItem) -> str | None:
    text = f"{mail.subject}\n{mail.body[:3000]}"
    for signal, rx in _PATTERNS:
        if rx.search(text):
            return signal
    return None


def is_recruiting_sender(mail: MailItem) -> bool:
    return bool(_RECRUITING_SENDER.search(mail.sender))


def next_status(current: str | None, signal: str) -> tuple[str | None, str]:
    """(new status or None for no change, note). Only ever moves forward."""
    target = SIGNAL_STATUS.get(signal)
    if target is None:
        return None, ""
    current = current or "Tailored"
    if current in ("Accepted", "Withdrawn"):
        return None, f"{signal} email after {current}: left as is"
    if target == "Rejected":
        if current == "Offer":
            return None, "conflict: rejection email after an Offer, check by hand"
        if current == "Rejected":
            return None, ""
        return "Rejected", ""
    if current == "Rejected":
        return None, f"conflict: {signal} email after a rejection, check by hand"
    if current == "Ghosted":
        return target, "revived: they replied after being marked Ghosted"
    if STAGE.get(target, 0) > STAGE.get(current, 0):
        return target, ""
    return None, ""


# ---------- new applications from confirmation emails ----------

def application_from_email(mail: MailItem) -> dict | None:
    """Company / role / source for an "application sent / received" email about an untracked job.

    Covers jobs sent through LinkedIn, Naukri, Aperture or a company's ATS that were never
    run through `apply`, so the tracker still counts every application. Conservative:
    returns None unless a company name can be read from the subject or opening line.
    """
    name, addr = parseaddr(mail.sender)
    text = f"{mail.subject}\n{mail.body[:400]}"
    company = role = ""
    for rx in _APPLIED_TO:
        if m := rx.search(text):
            company = m.group("company").strip(" -:'\"")
            role = (m.groupdict().get("role") or "").strip(" -:'\"")
            break
    if not company and _ATS.search(addr):
        company = _SENDER_NOISE.sub(" ", name).strip(" -:,")  # "Razorpay Hiring Team" -> "Razorpay"
    company = re.sub(r"\s+(?:and|for|via|on)\s.*$", "", company)
    company = re.sub(r"\s+", " ", company).strip()
    if not company or len(company) > 50 or company.lower() in {"us", "our team", "the company", "you"}:
        return None
    portal = _PORTAL.search(mail.sender)
    source = portal.group(0).capitalize() if portal else ("Company site" if _ATS.search(addr) else "Email / cold")
    source = {"Linkedin": "LinkedIn", "Aperture": "Other"}.get(source, source)
    return {"company": company, "role": role or "(role not in email)", "source": source}


# ---------- applying to the tracker ----------

def process(tracker: Tracker, mails: list[MailItem], *, my_address: str = "", dry_run: bool = False,
            auto_add: bool = True) -> SyncReport:
    report = SyncReport()
    apps = [a for a in tracker.applications() if a["status"] not in ("Lead",)]
    my_address = my_address.lower()
    for mail in sorted(mails, key=lambda m: m.received):
        if my_address and my_address in mail.sender.lower():
            continue  # our own sent mail
        app, companies = match_application(mail, apps)
        signal = classify(mail)
        if app is None:
            relevant = is_recruiting_sender(mail) or _PORTAL.search(mail.sender)
            new = application_from_email(mail) if (auto_add and signal == "Acknowledged" and relevant) else None
            if new and len(companies) <= 1:
                report.added.append((mail, new))
                if not dry_run:
                    received = mail.received.astimezone().replace(tzinfo=None)
                    app_id = tracker.add_application(
                        company=new["company"], role=new["role"], source=new["source"], status="Applied",
                        date_applied=received.date(), email_received="No",
                        notes=f"[{received:%d %b}] added from email: {mail.subject[:120]}")
                    tracker.log_email(received=received, **{"from": mail.sender[:200]}, subject=mail.subject[:250],
                                      app_id=app_id, company=new["company"], signal=signal,
                                      action="added to tracker from confirmation email", message_id=mail.message_id)
                    apps = [a for a in tracker.applications() if a["status"] not in ("Lead",)]
            elif len(companies) > 1 and (signal or is_recruiting_sender(mail)):
                report.ambiguous.append((mail, companies))
            elif signal and is_recruiting_sender(mail):
                report.unmatched.append(mail)
            continue
        if signal is None and not is_recruiting_sender(mail):
            continue  # a newsletter or promo from the same company, not about the application

        app = tracker.find(app["id"])  # re-read: an earlier email in this run may have changed it
        current = app["status"]
        new, note = next_status(current, signal) if signal else (None, "")
        change = Change(app_id=app["id"], company=app["company"], signal=signal, old_status=current,
                        new_status=new, mail=mail, note=note)
        if note.startswith("conflict"):
            report.conflicts.append(change)
        report.changes.append(change)
        if dry_run:
            continue

        received = mail.received.astimezone().replace(tzinfo=None)
        updates = {
            "email_received": "Yes",
            "last_email_date": received.date(),
            "last_email_from": mail.sender[:200],
            "last_email_subject": mail.subject[:250],
        }
        if signal:
            updates["email_signal"] = signal
        if new:
            updates["status"] = new
            if not app["date_applied"] and new != "Rejected":
                updates["date_applied"] = received.date()  # a reply proves it was sent
        tracker.update(app["id"], **updates)
        if new or note:
            tracker.append_note(app["id"], f"[{received:%d %b}] email: {signal or 'reply'}"
                                + (f" -> {new}" if new else "") + (f" ({note})" if note else ""))
        tracker.log_email(received=received, **{"from": mail.sender[:200]}, subject=mail.subject[:250], app_id=app["id"],
                          company=app["company"], signal=signal or "Other",
                          action=(f"{current} -> {new}" if new else (note or "logged")),
                          message_id=mail.message_id)
    return report


# ---------- Gmail over IMAP ----------

def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _body_text(msg: email.message.Message, limit: int = 6000) -> str:
    plain, html = [], []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        (plain if part.get_content_type() == "text/plain" else html if part.get_content_type() == "text/html" else []).append(text)
    text = "\n".join(plain) or BeautifulSoup("\n".join(html), "html.parser").get_text(" ")
    return re.sub(r"[ \t\r\f\v]+", " ", text)[:limit]


def parse_message(raw: bytes) -> MailItem:
    msg = email.message_from_bytes(raw)
    try:
        received = parsedate_to_datetime(msg.get("Date"))
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
    except Exception:
        received = datetime.now(timezone.utc)
    return MailItem(
        message_id=(msg.get("Message-ID") or "").strip(),
        received=received,
        sender=_decode(msg.get("From")),
        subject=_decode(msg.get("Subject")).replace("\n", " "),
        body=_body_text(msg),
    )


# Gmail-side filter: only mail that could be about a job application. Gmail applies it before
# anything is downloaded, so a mailbox of tens of thousands of emails costs a few seconds.
GMAIL_FILTER = ("-in:sent -in:chats -category:promotions -category:social -category:forums "
                "{application applied applying apply interview interviews assessment assignment candidate candidature "
                "offer recruiter recruiting recruitment hiring position role job career careers talent opportunity "
                "shortlisted unfortunately \"next steps\" \"thank you for\"}")


def _search(imap, since: date) -> list[bytes]:
    """Message numbers since `since`: Gmail's own search with the job filter when it's Gmail,
    else a plain IMAP date search."""
    try:
        status, data = imap.search(None, "X-GM-RAW", f'"after:{since:%Y/%m/%d} {GMAIL_FILTER.replace(chr(34), chr(92) + chr(34))}"')
        if status == "OK":
            return data[0].split() if data and data[0] else []
    except imaplib.IMAP4.error:
        pass  # not Gmail: no X-GM-RAW
    status, data = imap.search(None, "SINCE", since.strftime("%d-%b-%Y"))
    return data[0].split() if status == "OK" and data and data[0] else []


def _chunks(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def fetch_gmail(address: str, app_password: str, since: date, host: str = "imap.gmail.com",
                skip_ids: set[str] | None = None, max_messages: int = 2000, progress=None) -> list[MailItem]:
    """New mail since `since` that could be about a job. Headers come in batches first; full messages
    are downloaded only for mail not seen before and not sent by you. `progress(done, total)`."""
    skip_ids = skip_ids or set()
    out: list[MailItem] = []
    with imaplib.IMAP4_SSL(host) as imap:
        imap.login(address, app_password)
        # All Mail includes archived and labelled mail (filters often skip the inbox); fall back to INBOX.
        status, _ = imap.select('"[Gmail]/All Mail"', readonly=True)
        if status != "OK":
            imap.select("INBOX", readonly=True)
        ids = _search(imap, since)[-max_messages:]
        wanted: list[bytes] = []
        for batch in _chunks(ids, 200):
            status, rows = imap.fetch(b",".join(batch), "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM)])")
            if status != "OK":
                continue
            for row in rows:
                if not isinstance(row, tuple):
                    continue
                num = row[0].split()[0]
                head = email.message_from_bytes(row[1])
                mid = (head.get("Message-ID") or "").strip()
                if (mid and mid in skip_ids) or (address and address.lower() in (head.get("From") or "").lower()):
                    continue
                wanted.append(num)
        done = 0
        for batch in _chunks(wanted, 25):
            status, rows = imap.fetch(b",".join(batch), "(BODY.PEEK[])")
            if status == "OK":
                out += [parse_message(row[1]) for row in rows if isinstance(row, tuple)]
            done += len(batch)
            if progress:
                progress(done, len(wanted))
    return out


# ---------- state ----------

def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"last_sync": None, "processed": []}


def save_state(path: Path, state: dict) -> None:
    state["processed"] = state["processed"][-5000:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1), encoding="utf-8")


def default_since(state: dict, tracker: Tracker, override: str = "") -> date:
    """Where a sync starts reading: MAIL_SINCE if set; else just before the last sync; else the day
    you started applying (your first application, or the first job you added). Never your whole mailbox."""
    if override:
        return date.fromisoformat(override)
    if state.get("last_sync"):
        return date.fromisoformat(state["last_sync"]) - timedelta(days=1)  # overlap; processed ids dedupe
    as_date = lambda d: d.date() if isinstance(d, datetime) else d  # noqa: E731
    apps = tracker.applications()
    applied = [as_date(a["date_applied"]) for a in apps if a["date_applied"]]
    added = [as_date(a["date_added"]) for a in apps if a["date_added"]]
    first = min(applied or added or [date.today()])
    return first - timedelta(days=1)


def explain_login_error(exc: Exception) -> str:
    """A fix a person can act on, for the IMAP errors people actually hit."""
    text = str(exc)
    if re.search(r"AUTHENTICATIONFAILED|Invalid credentials|Application-specific password|Web login required", text, re.I):
        return ("Gmail refused the login. Gmail only accepts an App Password here: 16 letters from "
                "myaccount.google.com/apppasswords (2-Step Verification must be on), not your normal Google password. "
                "Put it in GMAIL_APP_PASSWORD in .env.")
    if re.search(r"IMAP access is disabled|not enabled for IMAP", text, re.I):
        return "IMAP is off: in Gmail, open Settings > See all settings > Forwarding and POP/IMAP > Enable IMAP."
    if isinstance(exc, OSError):
        return f"Couldn't reach Gmail ({type(exc).__name__}): check your internet connection."
    return f"Gmail sync failed: {text.splitlines()[0] if text else type(exc).__name__}"


def run_sync(settings, *, lock=None, since: date | None = None, dry_run: bool = False, auto_add: bool = True,
             fetch=None, progress=None) -> dict:
    """Read new mail and update the tracker. Network first, then the tracker under `lock`.

    Returns {report, mails, address, since}; raises RuntimeError with explain_login_error's text."""
    from contextlib import nullcontext

    address, password = settings.gmail_login
    if not (address and password):
        raise RuntimeError("Set GMAIL_APP_PASSWORD (a 16-letter Google App Password) and CANDIDATE_EMAIL in .env "
                           "to track replies from your Gmail.")
    state_path = settings.data_dir / "mail_state.json"
    state = load_state(state_path)
    since = since or default_since(state, Tracker(settings.tracker_path), settings.mail_since)
    try:
        kw = {"progress": progress} if progress else {}
        mails = (fetch or fetch_gmail)(address, password, since, settings.imap_host, skip_ids=set(state["processed"]), **kw)
    except Exception as exc:
        raise RuntimeError(explain_login_error(exc)) from exc
    with lock or nullcontext():
        t = Tracker(settings.tracker_path)
        report = process(t, mails, my_address=address, dry_run=dry_run, auto_add=auto_add)
        if not dry_run:
            t.save()
    if not dry_run:
        state["last_sync"] = date.today().isoformat()
        state["processed"] += [m.message_id for m in mails if m.message_id]
        save_state(state_path, state)
    return {"report": report, "mails": mails, "address": address, "since": since}
