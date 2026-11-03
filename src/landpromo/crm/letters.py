"""Landowner letter drafting from the templates in docs/templates/.

Without an API key the template is filled in directly. With a key, Claude
writes the site-specific paragraph and adjusts wording within the guardrails.
Every letter is a draft until a founder approves it.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from ..screening import MODEL, _strict_schema

REPO = Path(__file__).resolve().parents[3]
TEMPLATES = REPO / "docs" / "templates"


class _Blank(dict):
    def __missing__(self, key):
        return "[" + key.replace("_", " ") + "]"


def _template(name: str) -> str:
    text = (TEMPLATES / name).read_text()
    return re.sub(r"<!--.*?-->\s*", "", text, flags=re.S).strip()


def settings() -> dict:
    """Letterhead settings from the environment. Unset ones are left out, so they show as [placeholders]."""
    values = {
        "company_name": os.environ.get("CRM_COMPANY_NAME", ""),
        "company_address": os.environ.get("CRM_COMPANY_ADDRESS", ""),
        "sender_names": os.environ.get("CRM_SENDER_NAMES", ""),
        "reply_email": os.environ.get("CRM_REPLY_EMAIL", ""),
        "reply_phone": os.environ.get("CRM_REPLY_PHONE", ""),
        "area": os.environ.get("CRM_AREA", "East Dorset, South Wiltshire and South Hampshire"),
    }
    return {k: v for k, v in values.items() if v.strip()}


def _salutation(name: str) -> str:
    name = name.strip()
    if re.match(r"^(mr|mrs|ms|miss|dr)\b", name, re.I):
        return name
    return "Sir or Madam" if not name or any(w in name.lower() for w in ("ltd", "llp", "trust", "estate", "farms")) else name


def _describe(site_name: str) -> str:
    """'Land north of Mill Lane, Downton' -> 'north of Mill Lane'; 'Orchard Farm' -> 'at Orchard Farm'."""
    first = site_name.split(",")[0].strip()
    m = re.match(r"^(land|field|paddock|site)\s+(.*)$", first, re.I)
    return m.group(2) if m else f"at {first}"


def fields(site, owner) -> dict:
    land_use = "new homes" if site["land_use"] == "residential" else "employment use, such as small business units"
    settlement = site["name"].split(",")[-1].strip() if "," in site["name"] else "the village"
    return _Blank(
        settings(),
        date=date.today().strftime("%-d %B %Y"),
        landowner_name=owner["name"],
        landowner_address=owner["address"] or "",
        salutation=_salutation(owner["name"]),
        site_description=_describe(site["name"]),
        settlement=settlement,
        land_use_phrase=land_use,
        site_paragraph="[One or two sentences specific to this land: what is next to it, why it may suit "
        "development, any recent nearby permissions.]",
    )


def fill_template(site, owner) -> str:
    f = fields(site, owner)
    letter = _template("landowner-letter.md").format_map(f)
    return letter


def privacy_notice(site, owner) -> str:
    return _template("privacy-notice.md").format_map(fields(site, owner))


class SiteParagraph(BaseModel):
    paragraph: str


SYSTEM_PROMPT = """\
You write one short paragraph for a letter from a small, local land promotion \
business to a private landowner. The rest of the letter is fixed. Write two or \
three sentences that are specific to this piece of land, using only the facts \
given: where it sits, what is next to it, and any planning context supplied. \
Rules: British English; warm and plain; no pressure, urgency or deadlines; no \
mention of money, values or prices; no promises about planning outcomes; no \
flattery; do not invent facts. If the facts are thin, keep it short and general."""


def draft_with_claude(site, owner, client=None) -> str:
    import anthropic

    facts = {
        "site_name": site["name"],
        "authority": site["authority"],
        "land_use": site["land_use"],
        "gross_acres": site["gross_acres"],
        "local_plan_stage": site["local_plan_stage"],
        "founder_notes": site["notes"],
    }
    client = client or anthropic.Anthropic()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=2000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema",
                                                   "schema": _strict_schema(SiteParagraph)}},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Facts about the land: {facts}"}],
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"drafting stopped: {response.stop_reason}")
    text = next(b.text for b in response.content if b.type == "text")
    para = SiteParagraph.model_validate_json(text).paragraph.strip()
    f = fields(site, owner)
    f["site_paragraph"] = para
    return _template("landowner-letter.md").format_map(f)


def draft(site, owner, client=None) -> tuple[str, str]:
    """Return (letter body, how it was drafted)."""
    if client is not None or os.environ.get("ANTHROPIC_API_KEY"):
        return draft_with_claude(site, owner, client), "claude"
    return fill_template(site, owner), "template"
