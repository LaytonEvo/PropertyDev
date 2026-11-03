"""Command-line entry point: `landpromo <command>`."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import replace
from pathlib import Path

import requests

from . import constraints as c
from . import economics as econ
from . import screening as scr
from . import shortlist as sl


def _gbp(x: float) -> str:
    return f"£{x:,.0f}"


def cmd_economics(args) -> None:
    site, terms, plan = econ.load_scenario(args.scenario)
    if args.acres:
        site = replace(site, gross_acres=args.acres)
    result = econ.evaluate(site, terms, plan)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
        return
    print(f"Scenario: {args.scenario}  ({site.gross_acres} acres, {site.land_use})")
    print(f"  Dwellings:                 {result.dwellings}")
    print(f"  Gross land value:          {_gbp(result.gross_land_value)}")
    print(f"  Existing use value:        {_gbp(result.existing_use_value)}")
    for o in result.outcomes:
        print(f"  {o.name:<27}{o.probability:>5.0%}  profit {_gbp(o.profit):>12}")
    print(f"  Expected profit:           {_gbp(result.expected_profit)}")
    print(f"  Expected spend:            {_gbp(result.expected_costs)}")
    print(f"  Peak exposure:             {_gbp(result.peak_exposure)}")
    print(f"  Rough annual return:       {result.annualised_return:.0%} over ~{result.expected_years:.1f} yrs")
    print("  (placeholder values — confirm with a local agent)")


def cmd_min_acres(args) -> None:
    site, terms, plan = econ.load_scenario(args.scenario)
    acres = econ.min_viable_acres(site, terms, plan, args.target)
    if acres is None:
        print(f"No site up to 50 acres reaches expected profit {_gbp(args.target)}")
    else:
        print(f"Minimum gross site size for expected profit {_gbp(args.target)}: {acres} acres")


def _geocode_postcode(postcode: str) -> tuple[float, float]:
    pc = re.sub(r"\s+", "", postcode)
    r = requests.get(f"https://api.postcodes.io/postcodes/{pc}", timeout=20)
    r.raise_for_status()
    res = r.json()["result"]
    return res["latitude"], res["longitude"]


def cmd_screen(args) -> None:
    if args.geojson:
        geom = c.site_from_geojson(args.geojson)
        acres = c.area_acres(geom)
    else:
        if args.postcode:
            lat, lon = _geocode_postcode(args.postcode)
        elif args.lat is not None and args.lon is not None:
            lat, lon = args.lat, args.lon
        else:
            sys.exit("Give --geojson, --postcode, or --lat and --lon")
        if not args.acres:
            sys.exit("--acres is required unless --geojson is given")
        acres = args.acres
        geom = c.site_from_point(lat, lon, acres)
    layers = c.load_local_layers(args.layers)
    client = None if args.offline else c.PlanningDataClient()
    hits = c.lookup_constraints(geom, args.use, client=client, local_layers=layers)

    economics = None
    if args.scenario:
        site, terms, plan = econ.load_scenario(args.scenario)
        site = replace(site, gross_acres=acres, land_use=args.use)
        economics = econ.evaluate(site, terms, plan)

    inp = scr.ScreeningInput(
        name=args.name, land_use=args.use, gross_acres=acres,
        lat=geom.centroid.y, lon=geom.centroid.x,
        constraints=hits, economics=economics, notes=args.notes or "",
        sources_checked=([] if client is None else ["planning.data.gov.uk"]) + [l.name for l in layers],
    )
    rules = scr.rules_rating(inp, args.target)
    narrative = None if args.no_llm else scr.draft_narrative(inp, rules)
    md = scr.render_markdown(inp, rules, narrative)
    out = Path(args.out_dir) / f"{re.sub(r'[^a-z0-9]+', '-', args.name.lower()).strip('-')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(md)
    print(f"\nSaved to {out}")


def cmd_shortlist(args) -> None:
    layers = c.load_local_layers(args.layers)
    client = None if args.offline else c.PlanningDataClient()
    results = sl.shortlist(sl.read_settlements(args.csv), layers, client)
    md = sl.render_markdown(results, any(l.name.startswith("nutrient") for l in layers))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(md)
    print(f"\nSaved to {out}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="landpromo")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("economics", help="Expected-value model for one scenario")
    e.add_argument("scenario")
    e.add_argument("--acres", type=float)
    e.add_argument("--json", action="store_true")
    e.set_defaults(func=cmd_economics)

    m = sub.add_parser("min-acres", help="Smallest site meeting an expected-profit target")
    m.add_argument("scenario")
    m.add_argument("--target", type=float, default=50_000)
    m.set_defaults(func=cmd_min_acres)

    s = sub.add_parser("screen", help="Desktop appraisal for one site")
    s.add_argument("--name", required=True)
    s.add_argument("--use", choices=["residential", "industrial"], default="residential")
    s.add_argument("--geojson")
    s.add_argument("--postcode")
    s.add_argument("--lat", type=float)
    s.add_argument("--lon", type=float)
    s.add_argument("--acres", type=float)
    s.add_argument("--scenario", help="economics scenario TOML (acres and use are overridden)")
    s.add_argument("--target", type=float, default=50_000, help="expected-profit target (£)")
    s.add_argument("--notes")
    s.add_argument("--layers", default="data/layers.toml")
    s.add_argument("--offline", action="store_true", help="skip planning.data.gov.uk")
    s.add_argument("--no-llm", action="store_true", help="skip the Claude narrative")
    s.add_argument("--out-dir", default="out/appraisals")
    s.set_defaults(func=cmd_screen)

    t = sub.add_parser("shortlist", help="Tag settlements by nutrient catchment and designations")
    t.add_argument("csv", nargs="?", default="data/settlements.csv")
    t.add_argument("--layers", default="data/layers.toml")
    t.add_argument("--offline", action="store_true")
    t.add_argument("--out", default="out/settlement-shortlist.md")
    t.set_defaults(func=cmd_shortlist)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
