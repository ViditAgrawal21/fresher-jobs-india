"""Your answers to the questions application forms ask, learned as autofill meets them.

answers.yaml holds the standard details (name, CTC, notice period, education...). Every other
question a form asks, such as "How did you come to know about Sigmoid?", "Are you willing to
work from our Pune office 3 days a week?" or "Which of these languages do you know?", is added
to jobhunt/data/questions.yaml the first time autofill sees it, with the choices the form offers.
Fill in `answer` once and every later form that asks the same thing gets it, so autofill can
submit for you.

Write {company} or {role} in a question or an answer to stand for the job's company or title:
"Why do you want to work at {company}?" matches that question at every company.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

HEADER = """\
# Questions application forms asked that answers.yaml doesn't cover. Autofill adds new ones here.
# Fill each `answer` once and every later form that asks the same question gets it.
# - Dropdowns and radio buttons: copy one of the `choices`.
# - Checkbox lists: list the choices to tick, separated by commas.
# - Yes/No: write Yes or No.
# - {company} and {role} stand for the job's company and title.
# A required question with no answer stops autofill from submitting that form for you.
"""


def normalize(text: str, company: str = "") -> str:
    t = " ".join((text or "").split())
    if company:
        t = re.sub(re.escape(company.strip()), "{company}", t, flags=re.I)
    t = re.sub(r"[*✱]", "", t)
    return re.sub(r"[^a-z0-9{} ]+", "", t.lower()).strip()


class QuestionBank:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.items: list[dict] = []
        if self.path.exists():
            data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or []
            self.items = [x for x in data if isinstance(x, dict) and x.get("ask")]
        self.dirty = False

    def find(self, label: str, company: str = "") -> dict | None:
        from rapidfuzz import fuzz

        key = normalize(label, company)
        if len(key) < 3:
            return None
        best, score = None, 0
        for item in self.items:
            k = normalize(str(item["ask"]))
            s = 100 if k == key else fuzz.token_set_ratio(k, key) if min(len(k), len(key)) >= 12 else 0
            if s > score:
                best, score = item, s
        return best if score >= 92 else None

    def answer(self, label: str, company: str = "", role: str = "") -> str:
        item = self.find(label, company)
        if not item or item.get("answer") in (None, ""):
            return ""
        return str(item["answer"]).replace("{company}", company).replace("{role}", role)

    def learn(self, label: str, *, company: str = "", kind: str = "text", choices: list[str] | None = None,
              required: bool = False) -> None:
        """Add a question the first time it's seen; remember where else it shows up."""
        item = self.find(label, company)
        if item is None:
            ask = " ".join(label.split())
            if company:
                ask = re.sub(re.escape(company.strip()), "{company}", ask, flags=re.I)
            item = {"ask": ask, "answer": "", "type": kind}
            if choices:
                item["choices"] = [c for c in choices if c and c.strip()][:40]
            item["required"] = bool(required)
            item["seen"] = []
            self.items.append(item)
            self.dirty = True
        if company and company not in item.setdefault("seen", []):
            item["seen"].append(company)
            self.dirty = True
        if required and not item.get("required"):
            item["required"] = True
            self.dirty = True

    def unanswered(self) -> list[dict]:
        return [x for x in self.items if x.get("answer") in (None, "")]

    def save(self) -> None:
        if not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Unanswered required questions first: those are what stop autofill from submitting.
        items = sorted(self.items, key=lambda x: (x.get("answer") not in (None, ""), not x.get("required"), -len(x.get("seen", []))))
        body = yaml.safe_dump(items, sort_keys=False, allow_unicode=True, width=110, default_flow_style=None)
        self.path.write_text(HEADER + "\n" + body, encoding="utf-8")
        self.dirty = False
