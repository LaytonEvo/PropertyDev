# PropertyDev — land promotion tools

Internal tools for a small land promotion venture in East Dorset, South Wiltshire and South Hampshire. The build plan and the reasons behind it are in [docs/tech-plan-v2.md](docs/tech-plan-v2.md).

## What's here (build slice 1)

| Command | What it answers |
|---|---|
| `landpromo economics <scenario.toml>` | Expected profit for one site once refusal and appeal risk are included |
| `landpromo min-acres <scenario.toml> --target 50000` | Smallest site that meets an expected-profit target |
| `landpromo shortlist` | Which candidate settlements are residential tier 1 and which are industrial only (nutrient filter) |
| `landpromo screen ...` | Draft desktop appraisal for one site: constraints, economics, RED/AMBER/GREEN rating from fixed rules, and a Claude-written narrative |

All money values in `scenarios/*.toml` are **placeholders**. Replace them with local agent figures before quoting any output.

## Setup

```bash
pip install -e ".[dev]"
```

```bash
export ANTHROPIC_API_KEY=...   # only needed for the narrative in `screen`
```

Add the nutrient catchment layer as described in [data/README.md](data/README.md). Without it, residential screens are capped at AMBER and the shortlist shows a warning.

## Examples

```bash
landpromo economics scenarios/plan-v2-floor.toml
```

```bash
landpromo min-acres scenarios/residential-3ac.toml --target 50000
```

```bash
landpromo shortlist data/settlements.csv
```

```bash
landpromo screen --name "Paddock north of Romsey" --postcode "SO51 8XX" --acres 3 --scenario scenarios/residential-3ac.toml
```

```bash
landpromo screen --name "Field off A31" --geojson site.geojson --use industrial --scenario scenarios/industrial-5ac.toml --no-llm
```

Appraisals are saved to `out/appraisals/` as Markdown. Each is a draft for founder review.

## How screening works

1. **Constraints**: the site (plus a 100 m buffer) is checked against planning.data.gov.uk (SSSI, SAC/SPA/Ramsar, ancient woodland, scheduled monuments, Green Belt, National Landscapes, flood zones, conservation areas, listed buildings, TPOs, local planning authority) and against the local GeoJSON layers in `data/layers.toml`. Designations that are only nearby are downgraded to "moderate".
2. **Economics**: if `--scenario` is given, the site's acreage is run through the expected-value model.
3. **Rules rating**: any fatal on-site constraint, or negative expected profit, gives RED. A major constraint, profit below target, or an unchecked source gives AMBER. Otherwise GREEN.
4. **Narrative**: Claude explains the rating and lists checks and questions for the planning consultant. It can flag disagreement with the rating but cannot change it.

## Tests

```bash
pytest
```

## CRM

A small web app for the pipeline: sites, landowners, activity, spend and stage changes.

```bash
landpromo crm serve
```

Open http://127.0.0.1:8000. Data lives in `data/crm.sqlite3`, a single file that is git-ignored because it holds personal data. Back it up by copying the file.

What it enforces:
- **Spend caps per stage** (Identified £0, Outreach £200, Engaged £1,000, HoTs £1,000 + legal budget, Agreement + agreement budget). Spend over the cap needs a founder's initials.
- **Stage gates**: a site can't go to Outreach without a screen (not RED) and a linked landowner, or to HoTs without an expected-profit figure and a legal budget. A founder can override, and the override is logged.
- **Kill reasons** are required, so lessons are kept.
- **Follow-ups**: letters and emails get a 21-day follow-up automatically. A reply closes it.
- **Stalled sites**: no movement in 30 days puts the site on the dashboard for a kill/continue decision.
- **GDPR**: do-not-contact blocks outbound activity; letters to landowners with no privacy notice recorded trigger a warning.
- **Screening** from the site page, using the same engine as `landpromo screen`. The Claude narrative is added when `ANTHROPIC_API_KEY` is set.

Before running it anywhere other than your own computer, set `CRM_PASSWORD`. Settings are listed at the top of `src/landpromo/crm/app.py`.
