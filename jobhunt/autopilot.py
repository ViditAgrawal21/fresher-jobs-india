"""Autopilot: while the dashboard is open, it keeps the search moving without you clicking.

  - checks Gmail for replies every AUTOPILOT_MAIL_HOURS (2);
  - checks every Ready job is still open every AUTOPILOT_VERIFY_HOURS (12);
  - searches for new jobs every AUTOPILOT_DISCOVER_HOURS (24);
  - drafts follow-up emails in Gmail Drafts for applications with no reply after 7 days, every
    AUTOPILOT_FOLLOWUP_HOURS (24); you read and send them;
  - after each search, tailors a resume for the best new matches, up to AUTO_TAILOR_PER_DAY (5)
    a day: JD match of at least AUTO_TAILOR_MIN_SCORE (70)%, posted in the last FRESH_JOB_DAYS,
    in your places (remote India, remote open to India, your cities). They appear in Ready to
    apply; applying stays your click (Auto-apply), so nothing is sent without you starting it.

The switch and the times of the last runs are kept in jobhunt/data/autopilot.json, so a restart
picks up where it left off instead of running everything again.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, datetime

from jobhunt.settings import JobhuntSettings

log = logging.getLogger(__name__)


class Autopilot:
    def __init__(self, settings: JobhuntSettings, *, start_mail, start_check, start_discover, queue_tailor,
                 start_followups=None):
        self.settings = settings
        self.path = settings.data_dir / "autopilot.json"
        self.start = {"mail": start_mail, "verify": start_check, "discover": start_discover,
                      "followups": start_followups or (lambda: False)}
        self.queue_tailor = queue_tailor
        self.lock = threading.Lock()
        self.state = {"on": True, "last": {}, "tailored": {"day": "", "count": 0}, "log": []}
        if self.path.exists():
            try:
                self.state.update(json.loads(self.path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass

    # ---------- schedule ----------
    def hours(self, kind: str) -> float:
        s = self.settings
        return {"mail": s.autopilot_mail_hours, "verify": s.autopilot_verify_hours,
                "discover": s.autopilot_discover_hours, "followups": s.autopilot_followup_hours}[kind]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, indent=1), encoding="utf-8")

    def _note(self, text: str, now: float) -> None:
        self.state["log"] = (self.state.get("log", []) + [f"{datetime.fromtimestamp(now):%d %b %H:%M} {text}"])[-12:]

    def set_on(self, on: bool) -> None:
        with self.lock:
            self.state["on"] = bool(on)
            self._save()

    def tick(self, now: float | None = None) -> list[str]:
        """Start whatever is due. Returns what it started."""
        now = now or time.time()
        started = []
        with self.lock:
            if not self.state.get("on"):
                return []
            for kind in ("mail", "verify", "discover", "followups"):
                hours = self.hours(kind)
                if hours <= 0:
                    continue
                last = self.state["last"].get(kind, 0)
                if now - last < hours * 3600:
                    continue
                try:
                    if self.start[kind]() is False:  # not possible right now (no Gmail password): try later
                        continue
                except Exception as exc:
                    log.warning("autopilot %s failed to start: %s", kind, exc)
                    continue
                self.state["last"][kind] = now
                self._note({"mail": "checked email", "verify": "checked jobs are still open",
                            "discover": "searched for new jobs", "followups": "drafted follow-ups"}[kind], now)
                started.append(kind)
            if started:
                self._save()
        return started

    # ---------- tailoring the best new matches ----------
    def after_discover(self, leads: list[dict], now: float | None = None) -> list[int]:
        """Queue tailoring for the best new matches, within today's limit. Returns the lead rows queued."""
        now = now or time.time()
        s = self.settings
        with self.lock:
            if not self.state.get("on") or s.auto_tailor_per_day <= 0:
                return []
            today = date.fromtimestamp(now).isoformat()
            if self.state["tailored"].get("day") != today:
                self.state["tailored"] = {"day": today, "count": 0}
            room = s.auto_tailor_per_day - self.state["tailored"]["count"]
            picks = [l for l in leads
                     if (l.get("score") or 0) >= s.auto_tailor_min_score and l.get("where_rank", 9) <= 3
                     and (l.get("age") is None or l["age"] <= s.fresh_job_days) and l.get("decision", "New") == "New"]
            picks.sort(key=lambda l: (-(l.get("score") or 0), l.get("where_rank", 9), l.get("age") or 999))
            rows = [l["row"] for l in picks[:max(0, room)]]
            for l in picks[:max(0, room)]:
                self.queue_tailor(l["row"])
            if rows:
                self.state["tailored"]["count"] += len(rows)
                self._note(f"tailoring resumes for {len(rows)} new match(es)", now)
                self._save()
            return rows

    def status(self, now: float | None = None) -> dict:
        now = now or time.time()
        s = self.settings
        nxt = {}
        for kind in ("mail", "verify", "discover", "followups"):
            last = self.state["last"].get(kind)
            nxt[kind] = datetime.fromtimestamp(last + self.hours(kind) * 3600).strftime("%d %b %H:%M") if last else "soon"
        return {"on": bool(self.state.get("on")), "next": nxt, "log": list(reversed(self.state.get("log", [])))[:6],
                "tailored_today": self.state["tailored"]["count"] if self.state["tailored"].get("day") == date.today().isoformat() else 0,
                "plan": {"mail_hours": s.autopilot_mail_hours, "verify_hours": s.autopilot_verify_hours,
                         "discover_hours": s.autopilot_discover_hours, "followup_hours": s.autopilot_followup_hours,
                         "tailor_per_day": s.auto_tailor_per_day,
                         "tailor_min_score": s.auto_tailor_min_score}}

    def run_forever(self, every_s: int = 60) -> None:
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("autopilot tick failed")
            time.sleep(every_s)
