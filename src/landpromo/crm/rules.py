"""Pipeline stages, spend caps and stage-gate checks. Pure functions, no database."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

STAGES = [
    "Identified",
    "Outreach",
    "Engaged",
    "HoTs",
    "Agreement signed",
    "Application submitted",
    "Consent",
    "Sold",
    "Killed",
]
CLOSED_STAGES = {"Sold", "Killed"}
OUTBOUND_KINDS = {"Letter sent", "Email sent", "Call made"}
ACTIVITY_KINDS = [
    "Letter sent",
    "Email sent",
    "Call made",
    "Landowner replied",
    "Meeting",
    "Site visit",
    "Consultant input",
    "Council contact",
    "Note",
]
SPEND_CATEGORIES = [
    "Outreach (letters, postage, data)",
    "Title searches",
    "Planning consultant",
    "Technical surveys",
    "Architect / design",
    "Legal (ours)",
    "Legal (landowner's)",
    "Application fee",
    "Appeal",
    "Travel",
    "Other",
]
STALL_DAYS = 30
FOLLOW_UP_DAYS = 21  # Tech Architecture §6.2: second letter after 21 days

# What has to be true before a site leaves each stage (shown as a checklist).
EXIT_CRITERIA = {
    "Identified": ["Screen run and not RED", "Landowner identified (title bought if needed)"],
    "Outreach": ["Landowner replied with interest"],
    "Engaged": ["Planning consultant desktop view", "Expected profit meets target", "Landowner motivation understood"],
    "HoTs": ["Heads of terms signed", "Restriction registered on title", "Legal budget approved"],
    "Agreement signed": ["Promotion agreement completed", "Planning strategy agreed (allocation or application)"],
    "Application submitted": ["Decision received"],
    "Consent": ["Agent instructed", "Sale agreed"],
}


@dataclass
class SiteState:
    stage: str
    rating: str | None
    expected_profit: float | None
    legal_budget: float
    agreement_budget: float
    landowner_count: int


def spend_cap(site: SiteState, stage: str | None = None) -> float | None:
    """Cumulative spend allowed at a stage (Tech Plan v2). None means no cap applies."""
    stage = stage or site.stage
    caps = {
        "Identified": 0.0,
        "Outreach": 200.0,
        "Engaged": 1_000.0,
        "HoTs": 1_000.0 + (site.legal_budget or 0),
        "Agreement signed": 1_000.0 + (site.legal_budget or 0) + (site.agreement_budget or 0),
    }
    if stage in caps:
        return caps[stage]
    if stage in ("Application submitted", "Consent"):
        return caps["Agreement signed"]
    return None


def check_spend(site: SiteState, spent_so_far: float, amount: float, approved_by: str) -> str | None:
    """Return an error message if this spend breaks the cap and has no founder approval."""
    if site.stage in CLOSED_STAGES:
        return f"Site is {site.stage}; spend can't be added."
    cap = spend_cap(site)
    if cap is None or spent_so_far + amount <= cap or approved_by:
        return None
    return (
        f"This takes spend to £{spent_so_far + amount:,.0f}, over the £{cap:,.0f} cap for "
        f"the {site.stage} stage. A founder must approve it (enter initials)."
    )


def stage_change_problems(site: SiteState, new_stage: str, kill_reason: str = "") -> list[str]:
    """Reasons a stage change should not go ahead without a founder override."""
    problems = []
    if new_stage not in STAGES:
        return [f"Unknown stage '{new_stage}'"]
    if new_stage == "Killed":
        if not kill_reason.strip():
            problems.append("Give a kill reason, so the lesson is kept for future sites.")
        return problems
    if site.stage in CLOSED_STAGES:
        problems.append(f"Site is {site.stage}. Reopening needs a founder override.")
    old_i, new_i = STAGES.index(site.stage), STAGES.index(new_stage)
    if new_i > old_i + 1:
        problems.append(f"Skips stages ({site.stage} → {new_stage}).")
    if new_i >= STAGES.index("Outreach") > old_i:
        if site.rating is None:
            problems.append("No screen has been run on this site.")
        elif site.rating == "RED":
            problems.append("Screen rating is RED.")
        if site.landowner_count == 0:
            problems.append("No landowner linked to this site.")
    if new_i >= STAGES.index("HoTs") > old_i:
        if site.expected_profit is None:
            problems.append("No expected-profit figure recorded.")
        if not site.legal_budget:
            problems.append("No legal budget set (including the landowner's legal fees).")
    return problems


def is_stalled(stage: str, last_movement: date | None, today: date | None = None) -> bool:
    today = today or date.today()
    if stage in CLOSED_STAGES or last_movement is None:
        return False
    return (today - last_movement) > timedelta(days=STALL_DAYS)


def default_follow_up(kind: str, on: date) -> date | None:
    return on + timedelta(days=FOLLOW_UP_DAYS) if kind in ("Letter sent", "Email sent") else None
