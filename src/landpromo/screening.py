"""Site screening: constraints + economics -> rules rating, plus a Claude-drafted narrative.

The RED/AMBER/GREEN rating comes from the rules in this module so it can be
audited. Claude explains it and lists what to check. Every appraisal is a draft
for founder review.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import anthropic
from pydantic import BaseModel

from .constraints import ConstraintHit
from .economics import EconomicsResult

Rating = Literal["RED", "AMBER", "GREEN"]
MODEL = "claude-opus-5-5"


@dataclass
class ScreeningInput:
    name: str
    land_use: str
    gross_acres: float
    lat: float
    lon: float
    constraints: list[ConstraintHit]
    economics: EconomicsResult | None = None
    notes: str = ""
    sources_checked: list[str] = field(default_factory=list)  # e.g. planning.data.gov.uk, layer names


@dataclass
class RulesResult:
    rating: Rating
    reasons: list[str] = field(default_factory=list)


def rules_rating(
    inp: ScreeningInput,
    target_expected_profit: float = 50_000,
) -> RulesResult:
    reasons: list[str] = []
    on_site = [c for c in inp.constraints if not c.within_buffer_only]
    fatal = [c for c in on_site if c.severity == "fatal"]
    major = [c for c in on_site if c.severity == "major"]
    rating: Rating = "GREEN"
    if fatal:
        rating = "RED"
        reasons += [f"Fatal on-site constraint: {c.label}" for c in fatal]
    if major:
        rating = "RED" if rating == "RED" else "AMBER"
        reasons += [f"Major constraint: {c.label}" for c in major]
    if inp.economics is not None:
        ev = inp.economics.expected_profit
        if ev <= 0:
            rating = "RED"
            reasons.append(f"Expected profit is negative (£{ev:,.0f})")
        elif ev < target_expected_profit:
            rating = "RED" if rating == "RED" else "AMBER"
            reasons.append(
                f"Expected profit £{ev:,.0f} is below the £{target_expected_profit:,.0f} target"
            )
    missing = []
    if "planning.data.gov.uk" not in inp.sources_checked:
        missing.append("national designations and flood zones (planning.data.gov.uk)")
    if inp.land_use == "residential" and not any(
        s.startswith("nutrient") for s in inp.sources_checked
    ):
        missing.append("nutrient catchment")
    if inp.economics is None:
        missing.append("economics")
    if missing:
        if rating == "GREEN":
            rating = "AMBER"
        reasons.append("Not checked: " + "; ".join(missing))
    if not reasons:
        reasons.append("No fatal or major constraints found and economics meet target")
    return RulesResult(rating, reasons)


class Narrative(BaseModel):
    summary: list[str]  # three short paragraphs
    key_risks: list[str]
    checks_before_outreach: list[str]
    questions_for_planning_consultant: list[str]
    suggested_next_action: str
    agrees_with_rules_rating: bool
    disagreement_reason: str


SYSTEM_PROMPT = """\
You draft internal desktop appraisals for a small UK land promotion company \
working in East Dorset, South Wiltshire and South Hampshire. A founder reviews \
every appraisal before anything reaches a landowner.

Work only from the facts supplied. Where a fact that matters is missing (for \
example Local Plan stage, five-year land supply position, settlement boundary, \
access, or nearby appeal decisions), say so and list it as a check rather than \
assuming. The rating comes from fixed rules; explain it in plain English. If \
the facts suggest the rules have the rating wrong, set agrees_with_rules_rating \
to false and explain why, but do not change the rating.

Keep in mind: nutrient neutrality (Solent, Poole Harbour, River Avon) affects \
overnight accommodation, not most employment uses; 10% biodiversity net gain \
is mandatory; schemes of 10+ dwellings are major development and trigger \
affordable housing. Economics figures are model outputs built on placeholder \
values unless stated otherwise; treat them as indicative.

Write summary as exactly three short paragraphs: what the site is, why it has \
this rating, and what would change the view."""


def _facts(inp: ScreeningInput, rules: RulesResult) -> dict:
    return {
        "site": {
            "name": inp.name,
            "land_use": inp.land_use,
            "gross_acres": round(inp.gross_acres, 2),
            "centroid_lat_lon": [round(inp.lat, 5), round(inp.lon, 5)],
        },
        "constraints": [
            {
                "label": c.label,
                "name": c.name,
                "severity": c.severity,
                "on_site": not c.within_buffer_only,
            }
            for c in inp.constraints
        ],
        "economics": inp.economics.to_dict() if inp.economics else None,
        "rules_rating": rules.rating,
        "rules_reasons": rules.reasons,
        "sources_checked": inp.sources_checked,
        "notes_from_founder": inp.notes,
    }


def draft_narrative(
    inp: ScreeningInput,
    rules: RulesResult,
    client: anthropic.Anthropic | None = None,
) -> Narrative:
    client = client or anthropic.Anthropic()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        thinking={"type": "adaptive"},
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": _strict_schema(Narrative)},
        },
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": "Draft the appraisal for this site.\n\n"
                + json.dumps(_facts(inp, rules), indent=2),
            }
        ],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to draft this appraisal")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Appraisal was cut off (max_tokens reached)")
    text = next(b.text for b in response.content if b.type == "text")
    return Narrative.model_validate_json(text)


def _strict_schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    schema["additionalProperties"] = False
    schema["required"] = list(schema["properties"])
    return schema


def render_markdown(
    inp: ScreeningInput, rules: RulesResult, narrative: Narrative | None = None
) -> str:
    lines = [
        f"# Desktop appraisal: {inp.name}",
        "",
        f"*Draft for founder review, {date.today():%d %b %Y}. Not for issue to landowners.*",
        "",
        f"**Rating (rules): {rules.rating}**",
        "",
        *[f"- {r}" for r in rules.reasons],
        "",
        "## Site",
        f"- Land use: {inp.land_use}",
        f"- Gross area: {inp.gross_acres:.2f} acres",
        f"- Centroid: {inp.lat:.5f}, {inp.lon:.5f}",
        "",
    ]
    if narrative:
        lines += ["## Summary", "", *[p + "\n" for p in narrative.summary]]
        if not narrative.agrees_with_rules_rating:
            lines += [f"> **Analyst disagrees with rules rating:** {narrative.disagreement_reason}", ""]
    lines += ["## Constraints", ""]
    lines += [f"- {c.describe()}" for c in inp.constraints] or ["- None found in searched layers"]
    lines.append("")
    if inp.economics:
        e = inp.economics
        lines += [
            "## Economics (indicative, placeholder values)",
            "",
            "| Measure | Value |",
            "|---|---|",
            f"| Dwellings | {e.dwellings} |",
            f"| Gross land value with consent | £{e.gross_land_value:,.0f} |",
            f"| Expected profit to promoter | £{e.expected_profit:,.0f} |",
            f"| Expected spend | £{e.expected_costs:,.0f} |",
            f"| Peak exposure | £{e.peak_exposure:,.0f} |",
            f"| Rough annual return | {e.annualised_return:.0%} |",
            "",
            "| Outcome | Probability | Profit |",
            "|---|---|---|",
            *[f"| {o.name} | {o.probability:.0%} | £{o.profit:,.0f} |" for o in e.outcomes],
            "",
        ]
    if narrative:
        for title, items in [
            ("Key risks", narrative.key_risks),
            ("Checks before outreach", narrative.checks_before_outreach),
            ("Questions for the planning consultant", narrative.questions_for_planning_consultant),
        ]:
            lines += [f"## {title}", "", *[f"- {i}" for i in items], ""]
        lines += ["## Suggested next action", "", narrative.suggested_next_action, ""]
    return "\n".join(lines)
