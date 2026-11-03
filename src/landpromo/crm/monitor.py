"""Local Plan monitor: watch each council's Local Plan page and flag changes.

Each check fetches the page, reduces it to its visible text, and compares that
with the last check. A change is stored for founder review, with a Claude
summary of what changed when an API key is set. The first check of a page only
saves a baseline.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import threading
import time
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Literal

import requests
from pydantic import BaseModel

from ..screening import MODEL, _strict_schema
from .db import CRM

USER_AGENT = "LandCRM/0.1 (internal Local Plan monitor)"
CHECK_EVERY = timedelta(hours=24)
CHECK_FROM_HOUR = 7  # first daily check after 07:00 server time
MAX_TEXT = 200_000


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}
    BLOCK = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "br", "section", "article", "td"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


# Text that changes on every load and would flag false changes
_NOISE = re.compile(r"(cookie|last updated|page updated|©|copyright|\d{1,2}:\d{2}\s*(am|pm)?)", re.I)


def page_text(html: str) -> str:
    """Visible text of a page, one line per block, with obvious noise lines removed."""
    parser = _TextExtractor()
    parser.feed(html)
    lines = []
    for raw in "".join(parser.parts).splitlines():
        line = " ".join(raw.split())
        if line and not _NOISE.search(line):
            lines.append(line)
    return "\n".join(lines)[:MAX_TEXT]


def text_diff(old: str, new: str, limit: int = 6_000) -> str:
    """Added and removed lines only, capped in length."""
    out = []
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0):
        if line.startswith(("+++", "---", "@@")):
            continue
        out.append(line)
    return "\n".join(out)[:limit]


class ChangeSummary(BaseModel):
    relevance: Literal["high", "medium", "low"]
    summary: str
    plan_stage: str  # empty if the change doesn't say
    key_dates: list[str]  # e.g. "Call for sites closes 15 Dec 2026"


SYSTEM_PROMPT = """\
You review changes to a UK council's Local Plan web page for a small land \
promotion company. You get the lines added (+) and removed (-) since the last \
check. Say in one or two plain sentences what changed. Rate relevance: high for \
a new consultation, call for sites, change of plan stage or timetable, \
examination or adoption; medium for new evidence documents; low for \
navigation, formatting or unrelated news. Give the plan stage only if the page \
states it. List dates exactly as the page gives them. Use only the text given."""


def summarise_change(council: str, diff: str, client=None) -> ChangeSummary:
    import anthropic

    client = client or anthropic.Anthropic()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema",
                                                   "schema": _strict_schema(ChangeSummary)}},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Council: {council}\n\nChanges:\n{diff}"}],
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"summary stopped: {response.stop_reason}")
    text = next(b.text for b in response.content if b.type == "text")
    return ChangeSummary.model_validate_json(text)


def check_council(crm: CRM, council, session: requests.Session | None = None,
                  summariser=None) -> str:
    """Check one council's page. Returns a short status string."""
    url = (council["watch_url"] or "").strip()
    if not url:
        return "no page set"
    session = session or requests.Session()
    try:
        resp = session.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except requests.RequestException as e:
        status = f"fetch failed: {e.__class__.__name__}"
        crm.record_check(council["id"], status)
        return status
    if "html" in resp.headers.get("content-type", "html"):
        text = page_text(resp.text)
        digest = hashlib.sha256(text.encode()).hexdigest()
    else:  # PDF or other file: compare the bytes only
        text = f"[{resp.headers.get('content-type')}, {len(resp.content)} bytes]"
        digest = hashlib.sha256(resp.content).hexdigest()
    old_text, old_hash = council["last_text"], council["last_hash"]
    if old_hash is None:
        crm.record_check(council["id"], "baseline saved", digest, text)
        return "baseline saved"
    if digest == old_hash:
        crm.record_check(council["id"], "no change")
        return "no change"
    diff = text_diff(old_text or "", text) or "(file changed)"
    relevance, summary = "unknown", ""
    if summariser is None and os.environ.get("ANTHROPIC_API_KEY"):
        summariser = summarise_change
    if summariser is not None:
        try:
            s = summariser(council["name"], diff)
            relevance = s.relevance
            summary = s.summary
            if s.key_dates:
                summary += " Dates: " + "; ".join(s.key_dates)
            if s.plan_stage:
                summary += f" Stage: {s.plan_stage}."
        except Exception as e:  # keep the change even if the summary fails
            summary = f"(summary failed: {e.__class__.__name__})"
    if not summary:
        added = [l[1:] for l in diff.splitlines() if l.startswith("+")][:5]
        summary = "New text: " + " / ".join(added) if added else "Text removed from the page."
    crm.add_council_change(council["id"], summary, diff, relevance)
    crm.record_check(council["id"], "changed", digest, text)
    return "changed"


def due(council, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    if not council["watch_url"] or not council["is_target"]:
        return False
    if not council["last_checked"]:
        return True
    last = datetime.fromisoformat(council["last_checked"])
    return now - last >= CHECK_EVERY and now.hour >= CHECK_FROM_HOUR


def check_all(crm: CRM, only_due: bool = True) -> dict[str, str]:
    results = {}
    for council in crm.councils():
        if not council["watch_url"] or (only_due and not due(council)):
            continue
        results[council["name"]] = check_council(crm, council)
    return results


def start_background(crm: CRM, interval_s: int = 3600) -> threading.Thread:
    """Run due checks once an hour in a daemon thread."""

    def loop():
        while True:
            try:
                check_all(crm)
            except Exception as e:  # never let the monitor kill the web app
                print(f"council monitor error: {e}")
            time.sleep(interval_s)

    t = threading.Thread(target=loop, name="council-monitor", daemon=True)
    t.start()
    return t
