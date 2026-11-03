"""Site economics for a promotion agreement, including refusal and appeal risk.

All money values are GBP. Default values are placeholders: confirm them with a
local agent before relying on any output.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Literal

ACRE_TO_HA = 0.404686

LandUse = Literal["residential", "industrial"]
SplitBasis = Literal["net_proceeds", "uplift"]


@dataclass
class Site:
    gross_acres: float
    land_use: LandUse = "residential"
    net_developable_ratio: float = 0.65
    existing_use_value_per_acre: float = 12_000  # PLACEHOLDER (agricultural)
    # Residential
    density_dph: float = 30.0  # dwellings per net developable hectare
    affordable_pct: float = 0.35
    value_per_market_plot: float = 75_000  # PLACEHOLDER: serviced-equivalent land value
    value_per_affordable_plot: float = 15_000  # PLACEHOLDER
    s106_cil_per_dwelling: float = 10_000  # PLACEHOLDER: contributions borne by buyer
    nutrient_cost_per_dwelling: float = 0.0  # set when inside a nutrient catchment
    bng_cost_total: float = 0.0  # off-site biodiversity units, if needed
    # Industrial
    value_per_net_acre: float = 400_000  # PLACEHOLDER: consented employment land

    @property
    def net_acres(self) -> float:
        return self.gross_acres * self.net_developable_ratio

    @property
    def dwellings(self) -> int:
        if self.land_use != "residential":
            return 0
        return int(self.net_acres * ACRE_TO_HA * self.density_dph)

    def gross_land_value(self) -> float:
        """Price a buyer would pay for the site with consent, before sale costs."""
        if self.land_use == "industrial":
            value = self.net_acres * self.value_per_net_acre
        else:
            n = self.dwellings
            affordable = round(n * self.affordable_pct)
            market = n - affordable
            value = (
                market * self.value_per_market_plot
                + affordable * self.value_per_affordable_plot
                - n * (self.s106_cil_per_dwelling + self.nutrient_cost_per_dwelling)
            )
        return max(0.0, value - self.bng_cost_total)

    def existing_use_value(self) -> float:
        return self.gross_acres * self.existing_use_value_per_acre


@dataclass
class DealTerms:
    promoter_share: float = 0.25
    split_basis: SplitBasis = "net_proceeds"
    sale_cost_pct: float = 0.02  # agent and legal fees on disposal
    landowner_legal_fees: float = 7_500  # usually paid by promoter, recoverable


@dataclass
class PlanningScenario:
    application_cost: float = 50_000  # all promotion spend to a first decision
    p_consent_first: float = 0.55
    appeal_on_refusal: bool = True
    appeal_cost: float = 40_000  # hearing/written reps; an inquiry can cost far more
    p_appeal_success: float = 0.45
    years_to_sale: float = 4.0
    extra_years_if_appeal: float = 1.5


@dataclass
class Outcome:
    name: str
    probability: float
    promoter_costs: float
    promoter_receipt: float
    years: float

    @property
    def profit(self) -> float:
        return self.promoter_receipt - self.promoter_costs


@dataclass
class EconomicsResult:
    dwellings: int
    gross_land_value: float
    existing_use_value: float
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def expected_profit(self) -> float:
        return sum(o.probability * o.profit for o in self.outcomes)

    @property
    def expected_costs(self) -> float:
        return sum(o.probability * o.promoter_costs for o in self.outcomes)

    @property
    def peak_exposure(self) -> float:
        return max(o.promoter_costs for o in self.outcomes)

    @property
    def expected_years(self) -> float:
        return sum(o.probability * o.years for o in self.outcomes)

    @property
    def annualised_return(self) -> float:
        """Rough annual return on expected spend (not a true IRR: spend is staged)."""
        if self.expected_costs <= 0:
            return 0.0
        multiple = 1 + self.expected_profit / self.expected_costs
        if multiple <= 0:
            return -1.0
        return multiple ** (1 / max(self.expected_years, 0.1)) - 1

    def to_dict(self) -> dict:
        return {
            "dwellings": self.dwellings,
            "gross_land_value": round(self.gross_land_value),
            "existing_use_value": round(self.existing_use_value),
            "expected_profit": round(self.expected_profit),
            "expected_costs": round(self.expected_costs),
            "peak_exposure": round(self.peak_exposure),
            "expected_years": round(self.expected_years, 1),
            "annualised_return": round(self.annualised_return, 3),
            "outcomes": [asdict(o) | {"profit": round(o.profit)} for o in self.outcomes],
        }


def promoter_receipt_on_sale(site: Site, terms: DealTerms, costs: float) -> float:
    """What the promoter receives if the site sells with consent.

    Costs are recovered first from proceeds after sale costs, then the remainder is
    split. On an "uplift" basis the landowner keeps existing use value before the split.
    """
    proceeds = site.gross_land_value() * (1 - terms.sale_cost_pct)
    recovered = min(costs, proceeds)
    distributable = proceeds - recovered
    if terms.split_basis == "uplift":
        distributable = max(0.0, distributable - site.existing_use_value())
    return recovered + terms.promoter_share * distributable


def evaluate(site: Site, terms: DealTerms, plan: PlanningScenario) -> EconomicsResult:
    base_costs = plan.application_cost + terms.landowner_legal_fees
    p1 = plan.p_consent_first
    outcomes = [
        Outcome(
            "consent first time",
            p1,
            base_costs,
            promoter_receipt_on_sale(site, terms, base_costs),
            plan.years_to_sale,
        )
    ]
    if plan.appeal_on_refusal:
        appeal_costs = base_costs + plan.appeal_cost
        years = plan.years_to_sale + plan.extra_years_if_appeal
        pa = plan.p_appeal_success
        outcomes += [
            Outcome(
                "refused, appeal allowed",
                (1 - p1) * pa,
                appeal_costs,
                promoter_receipt_on_sale(site, terms, appeal_costs),
                years,
            ),
            Outcome("refused, appeal dismissed", (1 - p1) * (1 - pa), appeal_costs, 0.0, years),
        ]
    else:
        outcomes.append(Outcome("refused, no appeal", 1 - p1, base_costs, 0.0, plan.years_to_sale))
    return EconomicsResult(
        dwellings=site.dwellings,
        gross_land_value=site.gross_land_value(),
        existing_use_value=site.existing_use_value(),
        outcomes=outcomes,
    )


def min_viable_acres(
    site: Site,
    terms: DealTerms,
    plan: PlanningScenario,
    target_expected_profit: float,
    lo: float = 0.25,
    hi: float = 50.0,
) -> float | None:
    """Smallest gross acreage whose expected profit meets the target (bisection)."""

    def ev(acres: float) -> float:
        return evaluate(replace(site, gross_acres=acres), terms, plan).expected_profit

    if ev(hi) < target_expected_profit:
        return None
    if ev(lo) >= target_expected_profit:
        return lo
    for _ in range(60):
        mid = (lo + hi) / 2
        if ev(mid) >= target_expected_profit:
            hi = mid
        else:
            lo = mid
    return math.ceil(hi * 100) / 100  # round up so the result still meets the target


def load_scenario(path: str | Path) -> tuple[Site, DealTerms, PlanningScenario]:
    data = tomllib.loads(Path(path).read_text())
    return (
        Site(**data["site"]),
        DealTerms(**data.get("terms", {})),
        PlanningScenario(**data.get("planning", {})),
    )
