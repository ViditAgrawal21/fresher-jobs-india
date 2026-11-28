"""Follow-up emails, drafted into your Gmail Drafts for you to read and send.

For each application sent FOLLOWUP_AFTER_DAYS (7) or more days ago with no reply, it writes a short,
polite check-in: the role, when you applied, the strongest skills you share with the JD, and your
contact details. It goes to the recruiter when the tracker knows their address (the Recruiter /
Contact column, or the person who last emailed you, never a no-reply address); otherwise "To" is
left for you. Nothing is sent. Each application gets at most one draft a week.
"""

from __future__ import annotations

import imaplib
import re
import time
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from jobhunt.mailsync import load_state, save_state
from jobhunt.profile import load_profile
from jobhunt.settings import JobhuntSettings
from jobhunt.tracker import Tracker

NO_REPLY = re.compile(r"no-?reply|do-?not-?reply|donotreply|notifications?@|mailer|bounce|alerts?@|jobs-noreply", re.I)
NOT_A_NAME = re.compile(r"talent|career|recruit|hiring|team|jobs?|hr\b|people|notification|no.?reply|support|info|greenhouse|lever",
                        re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


def recipient(app) -> tuple[str, str]:
    """(email, first name) of the person to follow up with, or ("", "") when unknown."""
    contact = str(app["contact"] or "")
    m = EMAIL.search(contact)
    if m and not NO_REPLY.search(m.group(0)):
        name = contact[:m.start()].strip(" <(-,")
        return m.group(0), (name.split()[0] if name and not NOT_A_NAME.search(name) else "")
    name, addr = parseaddr(str(app["last_email_from"] or ""))
    if addr and not NO_REPLY.search(addr):
        return addr, (name.split()[0] if name and not NOT_A_NAME.search(name) else "")
    return "", ""


def compose(app, settings: JobhuntSettings, profile=None) -> EmailMessage:
    profile = profile or load_profile(settings.profile_path)
    to, first = recipient(app)
    applied = app["date_applied"].date() if isinstance(app["date_applied"], datetime) else app["date_applied"]
    skills = [s.strip() for s in str(app["matched"] or "").split(",") if s.strip()][:3]
    current = profile.experience[0] if profile.experience else None
    proof = ""
    if skills:
        proof = (f" My background fits what the role asks for: I work with {', '.join(skills[:-1]) + ' and ' + skills[-1] if len(skills) > 1 else skills[0]} "
                 f"in production" + (f", currently as a {current.title} at {current.company}." if current else "."))
    links = " | ".join(l.url for l in profile.links if l.label.lower() in ("portfolio", "linkedin"))
    contact_line = " | ".join(x for x in (settings.candidate_phone, settings.candidate_email) if x)
    body = (f"Hi {first or 'there'},\n\n"
            f"I applied for the {app['role']} role at {app['company']} on {applied:%d %B} and wanted to follow up on my application."
            f"{proof}\n\n"
            "Is there anything else I can share to help with the review? I'd be glad to talk whenever it suits you.\n\n"
            f"Thank you,\n{profile.name}\n" + (f"{contact_line}\n" if contact_line else "") + (f"{links}\n" if links else ""))
    msg = EmailMessage()
    msg["From"] = settings.gmail_login[0] or settings.candidate_email
    if to:
        msg["To"] = to
    msg["Subject"] = f"Following up on my application: {app['role']} ({profile.name})"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg.set_content(body)
    return msg


def due(settings: JobhuntSettings, today: date | None = None) -> list:
    """Applications that need a follow-up and haven't had a draft in the last 7 days."""
    today = today or date.today()
    drafted = load_state(settings.data_dir / "mail_state.json").get("drafted", {})
    out = []
    for a in Tracker(settings.tracker_path).followups(settings.followup_after_days, today):
        last = drafted.get(str(a["id"]))
        if last and (today - date.fromisoformat(last)).days < 7:
            continue
        out.append(a)
    return out


def _drafts_box(imap) -> str:
    """The Drafts folder, by its special-use flag, so it works in any Gmail language."""
    status, boxes = imap.list()
    for raw in boxes or []:
        line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
        if "\\Drafts" in line:
            m = re.search(r'"([^"]+)"\s*$', line) or re.search(r"(\S+)\s*$", line)
            if m:
                return m.group(1)
    return "[Gmail]/Drafts"


def create_drafts(settings: JobhuntSettings, *, lock=None, today: date | None = None) -> list[dict]:
    """Write a follow-up draft in Gmail for every application due one. Returns what it drafted."""
    from contextlib import nullcontext

    from jobhunt.mailsync import explain_login_error

    today = today or date.today()
    address, password = settings.gmail_login
    if not (address and password):
        raise RuntimeError("Set GMAIL_APP_PASSWORD (a 16-letter Google App Password) in .env to draft follow-ups in Gmail.")
    apps = due(settings, today)
    if not apps:
        return []
    profile = load_profile(settings.profile_path)
    done = []
    try:
        with imaplib.IMAP4_SSL(settings.imap_host) as imap:
            imap.login(address, password)
            box = _drafts_box(imap)
            for a in apps:
                msg = compose(a, settings, profile)
                status, _ = imap.append(f'"{box}"', "(\\Draft)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
                if status == "OK":
                    done.append({"id": a["id"], "company": a["company"], "role": a["role"], "to": msg.get("To", "")})
    except (imaplib.IMAP4.error, OSError) as exc:
        if not done:
            raise RuntimeError(explain_login_error(exc)) from exc
    if done:
        state_path = settings.data_dir / "mail_state.json"
        with lock or nullcontext():
            state = load_state(state_path)
            state.setdefault("drafted", {}).update({str(d["id"]): today.isoformat() for d in done})
            save_state(state_path, state)
            t = Tracker(settings.tracker_path)
            for d in done:
                t.update(d["id"], next_step="Send the follow-up (draft in Gmail)", next_step_date=today)
                t.append_note(d["id"], f"[{today:%d %b}] follow-up drafted in Gmail Drafts" + (f" to {d['to']}" if d["to"] else ""))
            t.save()
    return done
