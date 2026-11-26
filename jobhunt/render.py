"""Resume -> HTML -> one-page A4 PDF (Chromium via Playwright), plus an ATS text-layer check.

HTML instead of LaTeX: one Python dependency, works the same on Windows, and Chromium
writes a clean, selectable text layer that ATS parsers read without garbling.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pypdf import PdfReader

from jobhunt.profile import ResumeDoc

log = logging.getLogger(__name__)

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html", "j2"]),
)


def render_html(doc: ResumeDoc) -> str:
    return _env.get_template("resume.html.j2").render(r=doc)


class Renderer:
    """Keeps one browser open so the page-fitting loop doesn't relaunch Chromium each time."""

    def __init__(self, chromium_path: str = ""):
        self.chromium_path = chromium_path

    def __enter__(self) -> "Renderer":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on the machine
            raise RuntimeError("PDF rendering needs Playwright: pip install playwright && playwright install chromium") from exc
        self._pw = sync_playwright().start()
        kwargs = {"executable_path": self.chromium_path} if self.chromium_path else {}
        try:
            self._browser = self._pw.chromium.launch(**kwargs)
        except Exception as exc:
            self._pw.stop()
            raise RuntimeError(
                "Couldn't start Chromium. Run `playwright install chromium` once, "
                "or set CHROMIUM_PATH in .env to a Chrome/Chromium binary."
            ) from exc
        return self

    def __exit__(self, *exc) -> None:
        self._browser.close()
        self._pw.stop()

    def pdf(self, doc: ResumeDoc, out: Path) -> int:
        out.parent.mkdir(parents=True, exist_ok=True)
        page = self._browser.new_page()
        try:
            page.set_content(render_html(doc), wait_until="load")
            page.pdf(path=str(out), prefer_css_page_size=True, print_background=True)
        finally:
            page.close()
        return len(PdfReader(str(out)).pages)


def fit_one_page(doc: ResumeDoc, renderer: Renderer, out: Path, min_projects: int = 2) -> tuple[ResumeDoc, int, list[str]]:
    """Render, and while it spills onto page 2, tighten the layout and then drop the least relevant lines.

    Bullets and projects arrive ordered by relevance (tailoring puts the best first),
    so the last bullet of a list is always the cheapest one to cut.
    """
    doc = doc.model_copy(deep=True)
    cuts: list[str] = []
    pages = renderer.pdf(doc, out)
    while pages > 1:
        cut = _next_cut(doc, min_projects)
        if cut is None:
            log.warning("resume still %d pages after every allowed cut", pages)
            break
        cuts.append(cut)
        pages = renderer.pdf(doc, out)
    return doc, pages, cuts


def _drop_bullet(entry) -> str:
    entry.bullets.pop()
    entry.bullet_ids[-1:] = []
    return f"dropped a bullet from {entry.heading}"


def _next_cut(doc: ResumeDoc, min_projects: int) -> str | None:
    """Apply the cheapest remaining cut to `doc` in place and describe it; None when nothing is left to cut.

    Order: tighter layout; a 4th project; extra bullets on lower-ranked projects; past jobs
    down to 2 bullets (oldest first); a 3rd project; the top project down to 3 bullets; the
    current job down to 4; past jobs down to 1; the top project down to 2; the current job to 3.
    """
    first, rest = (doc.projects[0], doc.projects[1:]) if doc.projects else (None, [])
    past = doc.experience[1:]
    if doc.density == "normal":
        doc.density = "compact"
        return "compact layout"
    if len(doc.projects) > 3:
        return f"dropped project {doc.projects.pop().heading}"
    if (p := _last_with_extra_bullets(rest)) is not None:
        return _drop_bullet(p)
    if (e := _last_with_extra_bullets(past, keep=2)) is not None:
        return _drop_bullet(e)
    if len(doc.projects) > max(min_projects, 2):
        return f"dropped project {doc.projects.pop().heading}"
    if first is not None and len(first.bullets) > 3:
        return _drop_bullet(first)
    if doc.experience and len(doc.experience[0].bullets) > 4:
        return _drop_bullet(doc.experience[0])
    if (e := _last_with_extra_bullets(past)) is not None:
        return _drop_bullet(e)
    if len(doc.projects) > min_projects:
        return f"dropped project {doc.projects.pop().heading}"
    if first is not None and len(first.bullets) > 2:
        return _drop_bullet(first)
    if doc.experience and len(doc.experience[0].bullets) > 3:
        return _drop_bullet(doc.experience[0])
    return None


def _last_with_extra_bullets(entries, keep: int = 1):
    """The last entry (oldest job / lowest-ranked project) with more than `keep` bullets."""
    for e in reversed(entries):
        if len(e.bullets) > keep:
            return e
    return None


def ats_check(pdf: Path, doc: ResumeDoc) -> list[str]:
    """What an ATS would see: problems found in the PDF's text layer (empty list = clean)."""
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)
    flat = re.sub(r"\s+", " ", text)
    problems = []
    if "(cid:" in text or "�" in text:
        problems.append("garbled glyphs in the text layer")
    if doc.name not in flat:
        problems.append("name missing from the text layer")
    for c in doc.contact:
        digits = re.sub(r"\D", "", c)
        if "@" in c and c not in flat:
            problems.append(f"email {c} missing from the text layer")
        elif len(digits) >= 10 and digits[-10:] not in re.sub(r"\D", "", flat):
            problems.append("phone number missing from the text layer")
    for e in doc.experience:
        if e.heading not in flat:
            problems.append(f"{e.heading} missing from the text layer")
    return problems
