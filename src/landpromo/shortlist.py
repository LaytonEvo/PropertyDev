"""Settlement shortlist: tag candidate settlements by nutrient catchment and designations.

Answers the Phase 0 question from Business Plan §2.3: which settlements are
residential tier 1 (outside nutrient catchments) and which are industrial only.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from shapely.geometry import Point

from .constraints import ConstraintHit, LocalLayer, PlanningDataClient, lookup_constraints


@dataclass
class Settlement:
    name: str
    authority: str
    lat: float
    lon: float
    notes: str = ""


@dataclass
class SettlementResult:
    settlement: Settlement
    hits: list[ConstraintHit] = field(default_factory=list)

    @property
    def nutrient_catchments(self) -> list[str]:
        return [h.name or h.label for h in self.hits if h.source.startswith("nutrient")]

    @property
    def tier(self) -> str:
        if self.nutrient_catchments:
            return "Industrial only (nutrient catchment)"
        if any(h.severity in ("fatal", "major") for h in self.hits):
            return "Residential tier 2 (designation at centre)"
        return "Residential tier 1"


def read_settlements(path: str | Path) -> list[Settlement]:
    with open(path, newline="") as f:
        return [
            Settlement(
                name=row["name"],
                authority=row["authority"],
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                notes=row.get("notes", ""),
            )
            for row in csv.DictReader(f)
        ]


def shortlist(
    settlements: list[Settlement],
    local_layers: list[LocalLayer],
    client: PlanningDataClient | None = None,
) -> list[SettlementResult]:
    results = []
    for s in settlements:
        hits = lookup_constraints(
            Point(s.lon, s.lat), "residential", client=client, local_layers=local_layers, buffer_m=0
        )
        results.append(SettlementResult(s, hits))
    order = {"Residential tier 1": 0, "Residential tier 2 (designation at centre)": 1}
    return sorted(results, key=lambda r: (order.get(r.tier, 2), r.settlement.authority))


def render_markdown(results: list[SettlementResult], has_nutrient_layer: bool) -> str:
    lines = ["# Settlement shortlist", ""]
    if not has_nutrient_layer:
        lines += [
            "> **No nutrient catchment layer loaded.** Every settlement shows as tier 1 until",
            "> `data/layers/nutrient_catchments.geojson` is added (see data/README.md).",
            "",
        ]
    counts: dict[str, int] = {}
    for r in results:
        counts[r.tier] = counts.get(r.tier, 0) + 1
    lines += [f"- **{k}**: {v}" for k, v in counts.items()] + [""]
    lines += ["| Settlement | Authority | Tier | Nutrient catchment | Other designations | Notes |",
              "|---|---|---|---|---|---|"]
    for r in results:
        other = ", ".join(
            h.label for h in r.hits
            if not h.source.startswith("nutrient") and h.severity != "info"
        )
        lines.append(
            f"| {r.settlement.name} | {r.settlement.authority} | {r.tier} | "
            f"{', '.join(r.nutrient_catchments) or '-'} | {other or '-'} | {r.settlement.notes} |"
        )
    lines += ["", "*Checked at the settlement centre point only. Edge sites can fall either side of a catchment boundary; screen each site individually.*"]
    return "\n".join(lines)
