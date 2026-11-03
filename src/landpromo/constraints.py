"""Constraint lookups: planning.data.gov.uk API plus local GeoJSON layers.

planning.data.gov.uk is free and covers most national designations. Layers it
lacks (nutrient neutrality catchments, settlement boundaries) are loaded from
local GeoJSON files listed in data/layers.toml.
"""

from __future__ import annotations

import json
import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import requests
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.affinity import scale

from .economics import ACRE_TO_HA

Severity = Literal["fatal", "major", "moderate", "info"]

PLANNING_DATA_URL = "https://www.planning.data.gov.uk/entity.json"

# dataset -> (severity for residential, severity for industrial, label)
DATASETS: dict[str, tuple[Severity, Severity, str]] = {
    "site-of-special-scientific-interest": ("fatal", "fatal", "SSSI"),
    "special-area-of-conservation": ("fatal", "fatal", "Special Area of Conservation"),
    "special-protection-area": ("fatal", "fatal", "Special Protection Area"),
    "ramsar": ("fatal", "fatal", "Ramsar site"),
    "ancient-woodland": ("fatal", "fatal", "Ancient woodland"),
    "scheduled-monument": ("fatal", "fatal", "Scheduled monument"),
    "world-heritage-site": ("fatal", "fatal", "World Heritage Site"),
    "green-belt": ("major", "major", "Green Belt (check grey belt)"),
    "national-park": ("major", "major", "National Park"),
    "area-of-outstanding-natural-beauty": ("major", "major", "National Landscape (AONB)"),
    "conservation-area": ("moderate", "moderate", "Conservation area"),
    "listed-building-outline": ("moderate", "moderate", "Listed building"),
    "park-and-garden": ("moderate", "moderate", "Registered park and garden"),
    "tree-preservation-zone": ("moderate", "moderate", "Tree preservation order area"),
    "article-4-direction-area": ("info", "info", "Article 4 direction"),
    "brownfield-land": ("info", "info", "Brownfield land register"),
    "local-planning-authority": ("info", "info", "Local planning authority"),
}
FLOOD_DATASET = "flood-risk-zone"


@dataclass
class ConstraintHit:
    source: str  # dataset or local layer name
    label: str
    severity: Severity
    name: str = ""
    reference: str = ""
    within_buffer_only: bool = False  # touches the buffer but not the site itself

    def describe(self) -> str:
        where = " (nearby, not on site)" if self.within_buffer_only else ""
        name = f": {self.name}" if self.name else ""
        return f"[{self.severity}] {self.label}{name}{where}"


@dataclass
class LocalLayer:
    name: str
    label: str
    path: Path
    severity: dict[str, Severity]  # land_use -> severity; missing use = ignored
    name_field: str = "name"
    _geoms: list[tuple[BaseGeometry, dict]] = field(default_factory=list, repr=False)

    def load(self) -> "LocalLayer":
        if not self._geoms:
            fc = json.loads(self.path.read_text())
            self._geoms = [(shape(f["geometry"]), f.get("properties") or {}) for f in fc["features"]]
        return self

    def hits(self, geom: BaseGeometry, land_use: str) -> list[ConstraintHit]:
        sev = self.severity.get(land_use)
        if sev is None:
            return []
        out = []
        for g, props in self.load()._geoms:
            if g.intersects(geom):
                out.append(
                    ConstraintHit(
                        self.name, self.label, sev, str(props.get(self.name_field, "")), ""
                    )
                )
        return out


def load_local_layers(config_path: str | Path = "data/layers.toml") -> list[LocalLayer]:
    """Load the local layers listed in the config. Missing files are skipped with a warning."""
    config_path = Path(config_path)
    if not config_path.exists():
        return []
    cfg = tomllib.loads(config_path.read_text())
    layers = []
    for name, spec in cfg.get("layers", {}).items():
        path = (config_path.parent / spec["path"]).resolve()
        if not path.exists():
            print(f"warning: layer '{name}' not found at {path}; skipping")
            continue
        layers.append(
            LocalLayer(
                name=name,
                label=spec.get("label", name),
                path=path,
                severity=spec.get("severity", {}),
                name_field=spec.get("name_field", "name"),
            )
        )
    return layers


# --- geometry helpers (WGS84 lon/lat) -------------------------------------------

def _metre_factors(lat0: float) -> tuple[float, float]:
    """Metres per degree of longitude and latitude near lat0 (local flat-earth approximation)."""
    return 111_320 * math.cos(math.radians(lat0)), 110_574


def buffer_metres(geom: BaseGeometry, metres: float) -> BaseGeometry:
    """Buffer a lon/lat geometry by an approximate distance in metres (fine below ~1 km)."""
    fx, fy = _metre_factors(geom.centroid.y)
    in_m = scale(geom, xfact=fx, yfact=fy, origin=(0, 0))
    return scale(in_m.buffer(metres), xfact=1 / fx, yfact=1 / fy, origin=(0, 0))


def area_acres(geom: BaseGeometry) -> float:
    fx, fy = _metre_factors(geom.centroid.y)
    return geom.area * fx * fy / 10_000 / ACRE_TO_HA


def site_from_point(lat: float, lon: float, acres: float) -> BaseGeometry:
    """Approximate a site as a circle of the given area around a point."""
    radius = math.sqrt(acres * ACRE_TO_HA * 10_000 / math.pi)
    return buffer_metres(Point(lon, lat), radius)


def site_from_geojson(path: str | Path) -> BaseGeometry:
    data = json.loads(Path(path).read_text())
    if data.get("type") == "FeatureCollection":
        data = data["features"][0]
    if data.get("type") == "Feature":
        data = data["geometry"]
    return shape(data)


# --- planning.data.gov.uk --------------------------------------------------------

class PlanningDataClient:
    def __init__(self, session: requests.Session | None = None, timeout: float = 30.0):
        self.session = session or requests.Session()
        self.timeout = timeout

    def entities(self, geom: BaseGeometry, datasets: list[str]) -> list[dict]:
        params = [("dataset", d) for d in datasets] + [
            ("geometry", geom.wkt),
            ("geometry_relation", "intersects"),
            ("limit", "500"),
            ("exclude_field", "geometry"),
            ("exclude_field", "point"),
        ]
        resp = self.session.get(PLANNING_DATA_URL, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json().get("entities", [])


def _flood_severity(entity: dict, land_use: str) -> tuple[Severity, str]:
    level = str(entity.get("flood-risk-level", "")).strip()
    if level == "3":
        return "major", "Flood Zone 3"
    if level == "2":
        return ("moderate" if land_use == "residential" else "info"), "Flood Zone 2"
    return "info", f"Flood risk zone {level}".strip()


def lookup_constraints(
    site: BaseGeometry,
    land_use: str = "residential",
    client: PlanningDataClient | None = None,
    local_layers: list[LocalLayer] | None = None,
    buffer_m: float = 100.0,
) -> list[ConstraintHit]:
    """Find constraints that intersect the site, or a buffer around it."""
    hits: list[ConstraintHit] = []
    use_index = 0 if land_use == "residential" else 1
    if client is not None:
        search_area = buffer_metres(site, buffer_m) if buffer_m else site
        on_site_refs = {
            (e.get("dataset"), e.get("reference"))
            for e in client.entities(site, [*DATASETS, FLOOD_DATASET])
        }
        for e in client.entities(search_area, [*DATASETS, FLOOD_DATASET]):
            ds = e.get("dataset", "")
            nearby = (ds, e.get("reference")) not in on_site_refs
            if ds == FLOOD_DATASET:
                if nearby:
                    continue  # adjacent flood zones are not a site constraint
                sev, label = _flood_severity(e, land_use)
            elif ds in DATASETS:
                sev, label = DATASETS[ds][use_index], DATASETS[ds][2]
                if nearby and ds in ("local-planning-authority", "brownfield-land"):
                    continue
                if nearby and sev in ("fatal", "major"):
                    sev = "moderate"  # setting or impact issue, not an on-site designation
            else:
                continue
            hits.append(
                ConstraintHit(ds, label, sev, e.get("name", ""), str(e.get("reference", "")), nearby)
            )
    for layer in local_layers or []:
        hits.extend(layer.hits(site, land_use))
    return _dedupe(hits)


def _dedupe(hits: list[ConstraintHit]) -> list[ConstraintHit]:
    seen, out = set(), []
    for h in hits:
        key = (h.source, h.reference or h.name, h.label)
        if key not in seen:
            seen.add(key)
            out.append(h)
    order = {"fatal": 0, "major": 1, "moderate": 2, "info": 3}
    return sorted(out, key=lambda h: (order[h.severity], h.within_buffer_only, h.label))
